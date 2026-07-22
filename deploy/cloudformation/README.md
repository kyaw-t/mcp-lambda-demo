# CloudFormation harness

`agentcore-harness.yaml` provisions the **entire** demo as one stack — the same
thing the step-by-step boto3 scripts in `deploy/` do, but declaratively and in
one shot.

## What the stack creates

| Logical ID | Type | Role |
|---|---|---|
| `LambdaExecutionRole` | `AWS::IAM::Role` | the Lambda's own run-as role (logs) |
| `ToolLambda` | `AWS::Lambda::Function` | the six order-management tools |
| `UserPool` | `AWS::Cognito::UserPool` | inbound-auth identity provider |
| `ResourceServer` | `AWS::Cognito::UserPoolResourceServer` | declares the `mcp-orders/invoke` scope |
| `UserPoolDomain` | `AWS::Cognito::UserPoolDomain` | hosts the `/oauth2/token` endpoint |
| `UserPoolClient` | `AWS::Cognito::UserPoolClient` | machine-to-machine app client (client-credentials) |
| `GatewayRole` | `AWS::IAM::Role` | role the gateway assumes to invoke the Lambda (outbound auth) |
| `Gateway` | `AWS::BedrockAgentCore::Gateway` | the managed MCP server + JWT inbound auth |
| `GatewayTarget` | `AWS::BedrockAgentCore::GatewayTarget` | binds the Lambda + tool schema to the gateway |

## Prerequisites

- AWS CLI v2 configured for a region where AgentCore **and** the
  `AWS::BedrockAgentCore::*` CloudFormation types are available (e.g.
  `us-east-1`).
- An **existing S3 bucket** in that region to hold the Lambda zip. CloudFormation
  creates the function from S3, so the code must be uploaded first — the deploy
  script does this for you.
- A **globally-unique Cognito domain prefix** (e.g. `mcp-orders-<account-id>`).

## Deploy

One command (packages the Lambda, uploads it, deploys the stack, prints outputs):

```bash
export AWS_REGION=us-east-1
export CODE_BUCKET=my-existing-bucket
export COGNITO_DOMAIN_PREFIX=mcp-orders-123456789012   # must be globally unique
./deploy/cloudformation/deploy_stack.sh
```

<details>
<summary>Or do it by hand</summary>

```bash
# 1. package + upload the Lambda
cd src/lambda && zip -qr /tmp/function.zip . -x '*__pycache__*' && cd -
aws s3 cp /tmp/function.zip s3://my-existing-bucket/agentcore/function.zip

# 2. deploy the stack (IAM roles => CAPABILITY_NAMED_IAM)
aws cloudformation deploy \
  --stack-name agentcore-harness \
  --template-file deploy/cloudformation/agentcore-harness.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    LambdaCodeS3Bucket=my-existing-bucket \
    LambdaCodeS3Key=agentcore/function.zip \
    CognitoDomainPrefix=mcp-orders-123456789012
```
</details>

## After deploy: wire up the test client

The stack **Outputs** give you every value `client/test_client.py` needs, named
to match `deploy/config.sh`. The only value not in the outputs is the Cognito
**client secret** (CloudFormation doesn't expose it) — the
`ClientSecretRetrievalCommand` output prints the exact command to fetch it:

```bash
# see all outputs
aws cloudformation describe-stacks --stack-name agentcore-harness \
  --query "Stacks[0].Outputs" --output table

# fetch the secret
aws cognito-idp describe-user-pool-client \
  --user-pool-id <CognitoUserPoolId output> \
  --client-id <CognitoClientId output> \
  --query UserPoolClient.ClientSecret --output text
```

Paste the values into `deploy/config.sh`, then:

```bash
source deploy/config.sh
pip install -r client/requirements.txt
python client/test_client.py
```

## Parameters

| Parameter | Default | Notes |
|---|---|---|
| `ResourcePrefix` | `mcp-orders` | name prefix + Cognito resource-server id / scope namespace |
| `LambdaCodeS3Bucket` | — (required) | existing bucket holding the zip |
| `LambdaCodeS3Key` | `agentcore/function.zip` | object key of the zip |
| `CognitoDomainPrefix` | — (required) | globally-unique hosted-domain prefix |
| `GatewayName` | `OrdersDemoGateway` | gateway name (letters/digits/hyphens) |
| `TargetName` | `OrderTools` | **tool-name prefix** the agent sees: `OrderTools___get_order` |

> The Lambda strips the `<TargetName>___` prefix (triple underscore) before
> routing — see `src/lambda/handler.py`. If you change `TargetName`, no code
> change is needed; the handler discovers the prefix at runtime.

## Updating

- **Tool code changed:** re-run `deploy_stack.sh` — it re-uploads the zip. Note
  that if only the S3 object changes, you may need to also change `LambdaCodeS3Key`
  (or use versioned keys) for CloudFormation to detect the update and refresh the
  function. Simplest reliable pattern: use a content-hashed key.
- **Tool schema changed:** edit the `InlinePayload` in `agentcore-harness.yaml`
  and redeploy — the `GatewayTarget` updates with no interruption.

## Tear down

```bash
aws cloudformation delete-stack --stack-name agentcore-harness
aws cloudformation wait stack-delete-complete --stack-name agentcore-harness
```

## CloudFormation vs. the boto3 scripts

Both paths build the identical harness — pick one:

- **CloudFormation** (`this folder`): declarative, one stack, easy teardown,
  good for repeatable/GitOps deploys. Requires an S3 bucket for the code.
- **boto3 scripts** (`deploy/10_*`, `20_*`, `30_*`): imperative and incremental,
  nothing to pre-stage, good for learning exactly which API call does what. See
  [`docs/03-deployment-guide.md`](../../docs/03-deployment-guide.md).

## Notes

- `AWS::BedrockAgentCore::*` are recent resource types (added with AgentCore's
  GA). If `aws cloudformation deploy` reports an unknown type, your region
  doesn't have them yet — switch to a supported region or use the boto3 scripts.
- The `GatewayRole` trust policy includes an `aws:SourceAccount` confused-deputy
  guard. The gateway's inbound security is the Cognito JWT authorizer; to also
  make the endpoint reachable only from your VPC, see the private-networking
  discussion (interface VPC endpoint `com.amazonaws.<region>.bedrock-agentcore.gateway`).
