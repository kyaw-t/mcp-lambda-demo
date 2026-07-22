# AgentCore Harness — CloudFormation template

Full CloudFormation template that stands up the entire AgentCore Gateway harness
in one stack: the tool Lambda + execution role, Cognito (OAuth2 client-credentials)
inbound auth, the gateway IAM role, the gateway with CUSTOM_JWT inbound auth, and
a Lambda target carrying the six-tool inline schema.

Canonical file in the repo: `deploy/cloudformation/agentcore-harness.yaml`
Deploy with: `deploy/cloudformation/deploy_stack.sh` (packages the Lambda, uploads
to S3, deploys the stack). Narrative walkthrough: `docs/05-cloudformation-harness.md`.

## Deploy (quick)

```bash
export AWS_REGION=us-east-1
export CODE_BUCKET=my-existing-bucket                    # an S3 bucket you own, in AWS_REGION
export COGNITO_DOMAIN_PREFIX=mcp-orders-123456789012     # globally unique
./deploy/cloudformation/deploy_stack.sh
```

Deploy IAM roles require `--capabilities CAPABILITY_NAMED_IAM` (the script sets it).
The Cognito client secret is not a stack output — fetch it via the
`ClientSecretRetrievalCommand` output after deploy.

## Template

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: >-
  AgentCore Gateway harness: a Lambda-backed MCP server exposed through Amazon
  Bedrock AgentCore Gateway, with Cognito (OAuth2 client-credentials) inbound
  auth. Creates the tool Lambda + execution role, the gateway IAM role, a
  Cognito user pool/resource-server/domain/app-client, the gateway, and a
  Lambda target carrying the tool schema. See deploy/cloudformation/README.md.

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
Parameters:
  ResourcePrefix:
    Type: String
    Default: mcp-orders
    Description: Prefix applied to created resource names.
    AllowedPattern: "^[a-z0-9-]{1,30}$"

  LambdaCodeS3Bucket:
    Type: String
    Description: >-
      Name of an existing S3 bucket (in this region) that holds the packaged
      Lambda deployment zip. Upload it with deploy/cloudformation/deploy_stack.sh
      (or `aws s3 cp`) before creating the stack.

  LambdaCodeS3Key:
    Type: String
    Default: agentcore/function.zip
    Description: S3 object key of the packaged Lambda zip.

  CognitoDomainPrefix:
    Type: String
    Description: >-
      Globally-unique prefix for the Cognito hosted domain that serves the
      OAuth2 /oauth2/token endpoint. Must be unique across the region, e.g.
      mcp-orders-<your-account-id>.
    AllowedPattern: "^[a-z0-9-]{3,63}$"

  GatewayName:
    Type: String
    Default: OrdersDemoGateway
    Description: Name of the AgentCore gateway (letters/digits/hyphens only).
    AllowedPattern: "^([0-9a-zA-Z][-]?){1,100}$"

  TargetName:
    Type: String
    Default: OrderTools
    Description: >-
      Gateway target name. This becomes the tool-name prefix the agent sees:
      <TargetName>___<tool>. The Lambda strips this prefix (delimiter is three
      underscores) before routing. Letters/digits/hyphens only.
    AllowedPattern: "^([0-9a-zA-Z][-]?){1,100}$"

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
Resources:

  # ==== The tool Lambda ====================================================
  LambdaExecutionRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${ResourcePrefix}-lambda-role"
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

  ToolLambda:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: !Sub "${ResourcePrefix}-tool"
      Runtime: python3.12
      Handler: handler.lambda_handler
      Role: !GetAtt LambdaExecutionRole.Arn
      Timeout: 30
      MemorySize: 256
      Code:
        S3Bucket: !Ref LambdaCodeS3Bucket
        S3Key: !Ref LambdaCodeS3Key

  # ==== Inbound auth: Cognito (OAuth2 client-credentials) ==================
  UserPool:
    Type: AWS::Cognito::UserPool
    Properties:
      UserPoolName: !Sub "${ResourcePrefix}-pool"

  ResourceServer:
    Type: AWS::Cognito::UserPoolResourceServer
    Properties:
      UserPoolId: !Ref UserPool
      # The scope agents request becomes "<Identifier>/<ScopeName>" = "mcp-orders/invoke"
      Identifier: !Ref ResourcePrefix
      Name: !Sub "${ResourcePrefix} resource server"
      Scopes:
        - ScopeName: invoke
          ScopeDescription: Invoke MCP tools through the gateway

  UserPoolDomain:
    Type: AWS::Cognito::UserPoolDomain
    Properties:
      Domain: !Ref CognitoDomainPrefix
      UserPoolId: !Ref UserPool

  # The machine-to-machine app client. Uses client_credentials (no human login);
  # the agent presents this client's id + secret to get a JWT.
  UserPoolClient:
    Type: AWS::Cognito::UserPoolClient
    DependsOn: ResourceServer
    Properties:
      ClientName: !Sub "${ResourcePrefix}-agent-client"
      UserPoolId: !Ref UserPool
      GenerateSecret: true
      AllowedOAuthFlows:
        - client_credentials
      AllowedOAuthFlowsUserPoolClient: true
      AllowedOAuthScopes:
        - !Sub "${ResourcePrefix}/invoke"
      SupportedIdentityProviders:
        - COGNITO

  # ==== Outbound auth: the role the gateway assumes to invoke the Lambda ===
  GatewayRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${ResourcePrefix}-gateway-role"
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: bedrock-agentcore.amazonaws.com
            Action: sts:AssumeRole
            # Confused-deputy guard: only AgentCore acting for THIS account.
            Condition:
              StringEquals:
                aws:SourceAccount: !Ref AWS::AccountId
      Policies:
        - PolicyName: invoke-tool-lambda
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action: lambda:InvokeFunction
                Resource: !GetAtt ToolLambda.Arn

  # ==== The gateway (managed MCP server) ==================================
  Gateway:
    Type: AWS::BedrockAgentCore::Gateway
    Properties:
      Name: !Ref GatewayName
      RoleArn: !GetAtt GatewayRole.Arn
      ProtocolType: MCP
      AuthorizerType: CUSTOM_JWT
      AuthorizerConfiguration:
        CustomJWTAuthorizer:
          # Match the token's client_id claim (client-credentials sets no aud).
          AllowedClients:
            - !Ref UserPoolClient
          DiscoveryUrl: !Sub "https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}/.well-known/openid-configuration"
      Description: Demo gateway exposing an order-management Lambda as MCP tools.

  # ==== The Lambda target (schema + which Lambda backs it) ================
  GatewayTarget:
    Type: AWS::BedrockAgentCore::GatewayTarget
    Properties:
      GatewayIdentifier: !GetAtt Gateway.GatewayIdentifier
      Name: !Ref TargetName
      Description: Order-management Lambda target.
      CredentialProviderConfigurations:
        - CredentialProviderType: GATEWAY_IAM_ROLE
      TargetConfiguration:
        Mcp:
          Lambda:
            LambdaArn: !GetAtt ToolLambda.Arn
            ToolSchema:
              InlinePayload:
                - Name: get_product
                  Description: Look up a single product by its product_id and return its name, category, price and current stock level.
                  InputSchema:
                    Type: object
                    Properties:
                      product_id:
                        Type: string
                        Description: The product identifier, e.g. 'P-1001'.
                    Required:
                      - product_id
                - Name: search_products
                  Description: Search the product catalog by a free-text query matched against product name and category.
                  InputSchema:
                    Type: object
                    Properties:
                      query:
                        Type: string
                        Description: Free-text search term, e.g. 'headphones'.
                      max_results:
                        Type: integer
                        Description: Maximum number of products to return. Defaults to 10 if omitted.
                    Required:
                      - query
                - Name: get_order
                  Description: Retrieve a single order by its order_id, including its status, line items and total.
                  InputSchema:
                    Type: object
                    Properties:
                      order_id:
                        Type: string
                        Description: The order identifier, e.g. 'O-5001'.
                    Required:
                      - order_id
                - Name: list_orders
                  Description: List all orders belonging to a customer, optionally filtered by order status.
                  InputSchema:
                    Type: object
                    Properties:
                      customer_id:
                        Type: string
                        Description: The customer identifier, e.g. 'C-900'.
                      status:
                        Type: string
                        Description: Optional status filter. One of PENDING, SHIPPED, DELIVERED, CANCELLED (case-insensitive).
                    Required:
                      - customer_id
                - Name: create_order
                  Description: Create a new PENDING order for a customer from a list of line items. Validates stock and computes the total.
                  InputSchema:
                    Type: object
                    Properties:
                      customer_id:
                        Type: string
                        Description: The customer the order belongs to, e.g. 'C-900'.
                      items:
                        Type: array
                        Description: The line items to order.
                        Items:
                          Type: object
                          Properties:
                            product_id:
                              Type: string
                              Description: The product to order, e.g. 'P-1001'.
                            quantity:
                              Type: integer
                              Description: How many units to order. Positive integer.
                          Required:
                            - product_id
                            - quantity
                    Required:
                      - customer_id
                      - items
                - Name: cancel_order
                  Description: Cancel an existing order by order_id. Orders already DELIVERED or CANCELLED cannot be cancelled.
                  InputSchema:
                    Type: object
                    Properties:
                      order_id:
                        Type: string
                        Description: The order to cancel, e.g. 'O-5003'.
                      reason:
                        Type: string
                        Description: Optional free-text reason for the cancellation.
                    Required:
                      - order_id

# ---------------------------------------------------------------------------
# Outputs — the values the test client needs. Paste into deploy/config.sh.
# ---------------------------------------------------------------------------
Outputs:
  GatewayMcpUrl:
    Description: The MCP endpoint agents connect to (GATEWAY_MCP_URL).
    Value: !GetAtt Gateway.GatewayUrl

  GatewayId:
    Description: The gateway identifier (GATEWAY_ID).
    Value: !GetAtt Gateway.GatewayIdentifier

  CognitoUserPoolId:
    Description: COGNITO_USER_POOL_ID
    Value: !Ref UserPool

  CognitoClientId:
    Description: COGNITO_CLIENT_ID
    Value: !Ref UserPoolClient

  CognitoDiscoveryUrl:
    Description: COGNITO_DISCOVERY_URL
    Value: !Sub "https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}/.well-known/openid-configuration"

  CognitoTokenUrl:
    Description: COGNITO_TOKEN_URL (OAuth2 token endpoint)
    Value: !Sub "https://${CognitoDomainPrefix}.auth.${AWS::Region}.amazoncognito.com/oauth2/token"

  CognitoScope:
    Description: COGNITO_SCOPE
    Value: !Sub "${ResourcePrefix}/invoke"

  ClientSecretRetrievalCommand:
    Description: >-
      The client secret is not exposed as a stack output. Fetch it with this
      command (COGNITO_CLIENT_SECRET).
    Value: !Sub >-
      aws cognito-idp describe-user-pool-client --user-pool-id ${UserPool}
      --client-id ${UserPoolClient} --query UserPoolClient.ClientSecret --output text
```
