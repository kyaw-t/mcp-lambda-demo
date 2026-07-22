# Fix: AgentCore Gateway JWT authorizer can't reach our VPC-private JWKS / OIDC discovery URL

## Root cause (the key misunderstanding)

AgentCore Gateway is a **managed AWS service** that runs in an AWS-owned account —
**it is NOT in our VPC.** The fact that our EC2s can reach the JWKS endpoint is
irrelevant: the EC2s are in the VPC, the gateway is not. By default the gateway
resolves/fetches the `discoveryUrl` (and the JWKS URL it points to) **over the
public internet**, so a private/internal hostname fails. We must explicitly give
the gateway a network path into the VPC.

## The fix

Add a `privateEndpoint` block to the gateway's `customJWTAuthorizer`. This is
**not a new resource** — it's a property on the *existing* gateway. With
`managedVpcResource`, AgentCore itself provisions the VPC Lattice resource
gateway + ENIs in our subnets (via its service-linked role). We only reference
our **existing** VPC / subnets / security group by id.

### CloudFormation

Add under `AWS::BedrockAgentCore::Gateway` → `AuthorizerConfiguration.CustomJWTAuthorizer`:

```yaml
AuthorizerConfiguration:
  CustomJWTAuthorizer:
    AllowedClients: [<our-app-client-id>]
    DiscoveryUrl: https://idp.internal.example.com/.well-known/openid-configuration
    PrivateEndpoint:
      ManagedVpcResource:
        VpcIdentifier: vpc-xxxxxxxx
        SubnetIds: [subnet-xxxx, subnet-yyyy]
        SecurityGroupIds: [sg-xxxx]
        EndpointIpAddressType: IPV4
```

### boto3 (`create_gateway` / `update_gateway`)

```json
"authorizerConfiguration": {
  "customJWTAuthorizer": {
    "discoveryUrl": "https://idp.internal.example.com/.well-known/openid-configuration",
    "allowedClients": ["<our-app-client-id>"],
    "privateEndpoint": {
      "managedVpcResource": {
        "vpcIdentifier": "vpc-xxxxxxxx",
        "subnetIds": ["subnet-xxxx", "subnet-yyyy"],
        "securityGroupIds": ["sg-xxxx"],
        "endpointIpAddressType": "IPV4"
      }
    }
  }
}
```

## IAM prereqs

The principal that creates/updates the gateway needs these, or first-time setup
fails:

- `iam:CreateServiceLinkedRole` — for `AWSServiceRoleForBedrockAgentCoreIdentity`
- `ec2:CreateNetworkInterface`

## Reachability

The listed **subnets + security group** must reach the IdP on 443 (same
reachability our EC2 already has, but from *these* subnets/SG).

## Gotchas that cause it to STILL fail after adding the block

1. **Private TLS cert → silent TLS failure.** If the IdP serves JWKS with a
   **private-CA** cert, AgentCore rejects the handshake. It requires a
   **publicly trusted cert**. Sanctioned workaround: put an **internal ALB with a
   public ACM cert** in front of the IdP and point `discoveryUrl` at the ALB.
2. **HTTPS only** — `discoveryUrl` must be `https://`.
3. **Domain scope** — `privateEndpoint` applies to the **discovery URL's
   hostname**. If the JWKS or token endpoint is a *different* hostname, add
   `privateEndpointOverrides` per extra domain (self-managed Lattice only).

## Don't

Use `selfManagedLatticeResource` only if we need **cross-account** — that's the
only mode where we'd create a VPC Lattice `ResourceConfiguration` ourselves and
pass its ARN. For same-account, `managedVpcResource` needs no resource authoring.

## References

- AgentCore "Connect to private identity providers":
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-private-idp.html
- `AWS::BedrockAgentCore::Gateway` PrivateEndpoint CFN property:
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrockagentcore-gateway-privateendpoint.html
- Set up inbound authorization (JWT + private IdP):
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html
