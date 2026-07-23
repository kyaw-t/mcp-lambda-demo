# Custom JWT auth via an interceptor Lambda (when the gateway can't reach your IdP)

Handoff note. Problem: our IdP's JWKS / OIDC discovery URL is **VPC-private and
uses a private-CA cert**, so AgentCore Gateway's *managed* `CUSTOM_JWT`
authorizer can't validate tokens (it can't route to the private endpoint, and it
won't trust a private CA). Fix: **stop using the managed authorizer** — set the
gateway to `AuthorizerType: NONE` and validate the JWT ourselves in a **REQUEST
interceptor Lambda** that lives in our VPC.

## Why this beats fighting the managed authorizer

The interceptor is **our** Lambda in **our** account/VPC, so it can do the two
things the managed authorizer can't:

1. **Reach the private JWKS** — deploy it as a VPC Lambda in subnets that already
   reach the IdP (same reachability our EC2s have). No VPC Lattice `privateEndpoint`.
2. **Trust the private CA** — bundle the CA cert and point Python's TLS at it
   (`SSL_CERT_FILE`). No public-cert / ALB requirement.

Trade-off: with `NONE`, the gateway does **zero** auth of its own — **the
interceptor is the only gate, so it must fail closed.** Ideally also put the
gateway behind an interface VPC endpoint so it isn't publicly reachable either.

---

## How the interceptor contract works (MCP targets)

A **REQUEST interceptor** runs *before* the gateway calls your tool.

- It receives the inbound request. The **`Authorization` header is only included
  if `PassRequestHeaders: true`** in the interceptor config — this is mandatory
  for us, or we never see the token.
- **Allow:** return the request (pass-through).
- **Deny:** return a `transformedGatewayResponse` — the gateway sends it back
  immediately and never calls the tool.

Input payload the Lambda receives:

```json
{
  "interceptorInputVersion": "1.0",
  "mcp": {
    "rawGatewayRequest": { "body": "<raw_request_body>" },
    "gatewayRequest": {
      "path": "/mcp",
      "httpMethod": "POST",
      "headers": { "Authorization": "Bearer <jwt>", "Mcp-Session-Id": "<id>" },
      "body": { "jsonrpc": "2.0", "id": 1, "method": "tools/list" }
    }
  }
}
```

Output to **allow** (pass the original request through):

```json
{ "interceptorOutputVersion": "1.0",
  "mcp": { "transformedGatewayRequest": { "body": { "jsonrpc": "2.0", "id": 1, "method": "tools/list" } } } }
```

Output to **deny**:

```json
{ "interceptorOutputVersion": "1.0",
  "mcp": { "transformedGatewayResponse": {
      "statusCode": 401,
      "body": { "jsonrpc": "2.0", "id": 1, "error": { "code": -32001, "message": "Unauthorized" } } } } }
```

---

## The interceptor Lambda (Python stub — fails closed)

Validates the JWT against the private JWKS. Any error → **deny**. Package
`PyJWT[crypto]` and bundle your private CA PEM next to the code.

```python
import json
import logging
import os

import jwt
from jwt import PyJWKClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --- config (env vars) ---
JWKS_URL   = os.environ["JWKS_URL"]      # private, e.g. https://idp.internal/…/jwks
ISSUER     = os.environ["JWT_ISSUER"]    # expected iss
AUDIENCE   = os.environ.get("JWT_AUDIENCE")  # expected aud (optional)
ALGORITHMS = os.environ.get("JWT_ALGS", "RS256").split(",")

# Trust our private CA when fetching the JWKS over TLS. Bundle the PEM in the
# deployment package and set SSL_CERT_FILE to it (Python ssl reads this env).
#   e.g. SSL_CERT_FILE=/var/task/private-ca.pem
# PyJWKClient caches keys after first fetch; reuse across invocations.
_jwks_client = PyJWKClient(JWKS_URL, cache_keys=True)


def lambda_handler(event, context):
    try:
        req = event["mcp"]["gatewayRequest"]
        headers = req.get("headers") or {}
        # header keys can vary in case; find Authorization case-insensitively
        auth = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
        token = auth[7:] if auth.lower().startswith("bearer ") else auth
        if not token:
            return _deny(req, "Missing bearer token")

        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            issuer=ISSUER,
            audience=AUDIENCE,            # omit/None disables aud check
            options={"require": ["exp", "iss"]},
        )

        # ---- your authorization rules go here ----
        # e.g. require a scope, map claims to allowed tools, etc.
        # if "orders.read" not in claims.get("scope", "").split():
        #     return _deny(req, "insufficient_scope")

        logger.info("Authorized sub=%s", claims.get("sub"))
        return _allow(req)

    except Exception as exc:                       # FAIL CLOSED on anything
        logger.warning("JWT validation failed: %s", exc)
        return _deny(event.get("mcp", {}).get("gatewayRequest", {}), "Unauthorized")


def _allow(req):
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": req.get("body")}},
    }


def _deny(req, message):
    req_id = (req.get("body") or {}).get("id", None)
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 401,
                "body": {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32001, "message": message},
                },
            }
        },
    }
```

---

## CloudFormation wiring

### 1. The gateway: `NONE` inbound + the interceptor

```yaml
  Gateway:
    Type: AWS::BedrockAgentCore::Gateway
    Properties:
      Name: !Ref GatewayName
      RoleArn: !GetAtt GatewayRole.Arn
      ProtocolType: MCP
      AuthorizerType: NONE                         # <-- gateway does no auth; interceptor does
      InterceptorConfigurations:
        - InterceptionPoints: [REQUEST]            # REQUEST | RESPONSE (1–2)
          InputConfiguration:
            PassRequestHeaders: true               # REQUIRED so we get the Authorization header
          Interceptor:
            Lambda:
              Arn: !GetAtt AuthInterceptorLambda.Arn
      Description: Order-management gateway (custom auth via interceptor).
```

### 2. The interceptor Lambda (in the VPC that can reach the IdP)

```yaml
  AuthInterceptorLambda:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: !Sub "${ResourcePrefix}-auth-interceptor"
      Runtime: python3.12
      Handler: interceptor.lambda_handler
      Role: !GetAtt InterceptorRole.Arn
      Timeout: 10
      MemorySize: 256
      Environment:
        Variables:
          JWKS_URL: !Ref JwksUrl
          JWT_ISSUER: !Ref JwtIssuer
          JWT_AUDIENCE: !Ref JwtAudience
          SSL_CERT_FILE: /var/task/private-ca.pem   # bundled CA so TLS to the private JWKS is trusted
      VpcConfig:                                    # <-- puts the Lambda in your VPC
        SubnetIds: !Ref PrivateSubnetIds            # subnets that can reach the IdP
        SecurityGroupIds: !Ref InterceptorSgIds     # SG allowing egress to the IdP:443
      Code:
        S3Bucket: !Ref LambdaCodeS3Bucket
        S3Key: !Ref InterceptorCodeS3Key            # zip incl. interceptor.py, PyJWT, private-ca.pem

  InterceptorRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${ResourcePrefix}-interceptor-role"
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: { Service: lambda.amazonaws.com }
            Action: sts:AssumeRole
      ManagedPolicyArns:
        # VPC ENI perms for a VPC Lambda + basic logging
        - arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
```

### 3. Let the gateway invoke the interceptor

The gateway invokes the interceptor with its own role (same pattern as Lambda
targets). Add the permission to the **gateway** role:

```yaml
  # add this statement to GatewayRole's inline policy
  - Effect: Allow
    Action: lambda:InvokeFunction
    Resource: !GetAtt AuthInterceptorLambda.Arn
```

> VERIFY: interceptor invoke permissions aren't spelled out as explicitly as
> target invoke perms in the docs. If the gateway role approach is rejected, add
> a resource-based `AWS::Lambda::Permission` on the interceptor granting
> `lambda:InvokeFunction` to `bedrock-agentcore.amazonaws.com` (SourceAccount =
> your account) instead.

### 4. New parameters

```yaml
  JwksUrl:        { Type: String,             Description: Private JWKS URL, e.g. https://idp.internal/…/jwks }
  JwtIssuer:      { Type: String,             Description: Expected iss claim }
  JwtAudience:    { Type: String, Default: "", Description: Expected aud claim (blank to skip) }
  PrivateSubnetIds: { Type: List<AWS::EC2::Subnet::Id> }
  InterceptorSgIds: { Type: List<AWS::EC2::SecurityGroup::Id> }
  InterceptorCodeS3Key: { Type: String, Default: agentcore/interceptor.zip }
```

---

## Packaging the interceptor

`PyJWT` isn't in the Lambda runtime, so build a real package:

```bash
mkdir build && cp interceptor.py private-ca.pem build/
pip install "PyJWT[crypto]" -t build/
( cd build && zip -qr ../interceptor.zip . )
aws s3 cp interceptor.zip s3://<bucket>/agentcore/interceptor.zip
```

`private-ca.pem` = your private CA (or the full chain) so the TLS fetch of the
JWKS is trusted. If your IdP already has a publicly trusted cert, drop the PEM
and the `SSL_CERT_FILE` env var.

---

## Gotchas

1. **Fail closed.** `NONE` means the interceptor is the *only* auth. Any
   exception, missing token, or bad claim → deny. The stub above does this.
2. **`PassRequestHeaders: true` is mandatory** — without it the Lambda gets no
   `headers`, so no token to validate.
3. **Layer in network isolation.** A `NONE` gateway is otherwise open; put it
   behind the gateway interface VPC endpoint so only your network can reach it.
4. **Hot path.** The interceptor fires per MCP message (`tools/list`,
   `tools/call`). Keep it fast; `PyJWKClient` caches keys after the first fetch.
5. **6 MB Lambda payload limit** applies to interceptor invokes too — not an
   issue for request auth, but relevant if you ever add a RESPONSE interceptor
   over large tool outputs.
6. **Don't log tokens.** Log `sub`/decision, never the raw JWT.
7. **VERIFY** the gateway→interceptor invoke permission model (see step 3) and
   confirm `InterceptionPoints` values (`REQUEST` / `RESPONSE`) against your
   provider version.

---

## References

- Types of interceptors (payload contracts): https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html
- Offloaded inbound auth (`NONE` / `AUTHENTICATE_ONLY` + interceptor): https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html
- Fine-grained access control with interceptors (blog): https://aws.amazon.com/blogs/machine-learning/apply-fine-grained-access-control-with-bedrock-agentcore-gateway-interceptors/
- `AWS::BedrockAgentCore::Gateway` InterceptorConfigurations (CFN): https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrockagentcore-gateway-gatewayinterceptorconfiguration.html
