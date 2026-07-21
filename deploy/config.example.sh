# Copy this file to `config.sh` and edit the values, then `source config.sh`
# before running the deploy scripts. `config.sh` is git-ignored.
#
#   cp deploy/config.example.sh deploy/config.sh
#   $EDITOR deploy/config.sh
#   source deploy/config.sh

# ---- AWS basics -----------------------------------------------------------
export AWS_REGION="us-east-1"
# Your 12-digit account id. `aws sts get-caller-identity --query Account --output text`
export ACCOUNT_ID="123456789012"

# ---- Names (change only if you want to; the scripts key off these) --------
export LAMBDA_FUNCTION_NAME="mcp-orders-tool"
export LAMBDA_ROLE_NAME="mcp-orders-tool-lambda-role"
export GATEWAY_ROLE_NAME="mcp-orders-gateway-role"
export GATEWAY_NAME="OrdersDemoGateway"
export TARGET_NAME="OrderTools"          # becomes the tool-name prefix: OrderTools___get_order

# ---- Cognito (populated by 20_setup_cognito.py; leave blank at first) ------
export COGNITO_USER_POOL_ID=""
export COGNITO_CLIENT_ID=""
export COGNITO_CLIENT_SECRET=""
export COGNITO_DISCOVERY_URL=""          # .../.well-known/openid-configuration
export COGNITO_TOKEN_URL=""              # .../oauth2/token
export COGNITO_SCOPE=""                  # e.g. mcp-orders/invoke

# ---- Gateway (populated by 30_setup_gateway.py; leave blank at first) ------
export GATEWAY_ID=""
export GATEWAY_MCP_URL=""                # https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
