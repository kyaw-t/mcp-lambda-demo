# AgentCore Harness resource — the managed agent, wired to our Gateway (IAM auth)

Handoff note. Context: we already have an AgentCore **Gateway** + Lambda target
(the tool layer). This doc adds the **`AWS::BedrockAgentCore::Harness`** resource
— the actual managed agent — and wires it to that gateway using **IAM (SigV4)
auth** on the agent→gateway hop.

---

## 1. What the Harness is, and how it differs from the Gateway

`AWS::BedrockAgentCore::Harness` is a **managed agent loop**. You declare a
model + system prompt + tools inline, and AgentCore runs the entire agent
(orchestration, tool-calling, memory, response generation) for you — each
session in an isolated microVM with shell/filesystem access. **It is the agent.**

Three distinct AgentCore primitives — don't conflate them:

| Primitive | Role |
|---|---|
| `::Gateway` + `::GatewayTarget` | serves tools over MCP (our Lambda tools) |
| **`::Harness`** | the managed agent that *uses* tools and runs the loop |
| `::Runtime` | raw compute to host your *own* agent code (Harness runs on top of this; you don't manage it directly when using Harness) |

```
end user / app ──▶ Harness (managed agent loop: model + prompt + tools)
                     │  agent decides to call a tool
                     ▼
                   Gateway (MCP)  ──▶  Lambda tools
```

We had the bottom half (Gateway + Lambda). The Harness is the top half.

---

## 2. The auth model we're using (IAM / SigV4)

The Harness references a gateway with `HarnessAgentCoreGatewayConfig`, whose
`OutboundAuth` **defaults to `AWS_IAM` (SigV4)**. So for IAM auth we do the
matching thing on both sides:

- **Gateway inbound:** set `AuthorizerType: AWS_IAM` (was `CUSTOM_JWT`). The
  gateway now authorizes callers by IAM identity + SigV4 signature.
- **Harness → Gateway:** leave `OutboundAuth` unset (defaults to `AWS_IAM`). The
  Harness signs the call with its **execution role**.
- **Permission:** the Harness execution role needs
  `bedrock-agentcore:InvokeGateway` on the gateway ARN.

Consequence: with `AWS_IAM` inbound, **Cognito is no longer used for this path** —
you can drop the UserPool/ResourceServer/Domain/Client resources if the Harness
is the only caller. (`AuthorizerType` is a single value per gateway; you can't
run JWT and IAM inbound on the same gateway at once.)

---

## 3. CloudFormation to add / change

### 3a. Change the existing Gateway to IAM inbound

Replace the gateway's `AuthorizerType` + `AuthorizerConfiguration` block with:

```yaml
  Gateway:
    Type: AWS::BedrockAgentCore::Gateway
    Properties:
      Name: !Ref GatewayName
      RoleArn: !GetAtt GatewayRole.Arn      # outbound: gateway -> Lambda (unchanged)
      ProtocolType: MCP
      AuthorizerType: AWS_IAM               # <-- was CUSTOM_JWT
      # No AuthorizerConfiguration needed for AWS_IAM.
      Description: Order-management gateway (IAM inbound).
```

`GatewayTarget` is unchanged (still `GATEWAY_IAM_ROLE` outbound to the Lambda).

### 3b. Harness execution role

The role the managed agent runs as. It must (a) call the gateway, (b) invoke the
Bedrock model, (c) write logs.

```yaml
  HarnessRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub "${ResourcePrefix}-harness-role"
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: bedrock-agentcore.amazonaws.com
            Action: sts:AssumeRole
            Condition:
              StringEquals:
                aws:SourceAccount: !Ref AWS::AccountId
      Policies:
        - PolicyName: harness-permissions
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              # (a) call our gateway
              - Effect: Allow
                Action: bedrock-agentcore:InvokeGateway
                Resource: !GetAtt Gateway.GatewayArn
              # (b) invoke the model the agent loop uses
              - Effect: Allow
                Action:
                  - bedrock:InvokeModel
                  - bedrock:InvokeModelWithResponseStream
                  - bedrock:Converse
                  - bedrock:ConverseStream
                Resource: "*"        # tighten to the specific model / inference-profile ARN
              # (c) logs
              - Effect: Allow
                Action:
                  - logs:CreateLogGroup
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: !Sub "arn:aws:logs:${AWS::Region}:${AWS::AccountId}:*"
```

### 3c. The Harness resource

```yaml
  AgentHarness:
    Type: AWS::BedrockAgentCore::Harness
    Properties:
      HarnessName: OrdersAgent                     # ^[a-zA-Z][a-zA-Z0-9_]{0,39}$
      ExecutionRoleArn: !GetAtt HarnessRole.Arn
      Model:
        BedrockModelConfig:
          ModelId: !Ref ModelId                    # a Bedrock model id / inference profile
          Temperature: 0.2
          # ApiFormat: converse_stream             # optional: converse_stream | responses | chat_completions
      SystemPrompt:
        - Text: >-
            You are an order-management assistant. Use the order tools to look up
            products and orders, create orders, and cancel orders. Always confirm
            order ids before mutating anything.
      Tools:
        - Type: agentcore_gateway                  # <-- the gateway tool type
          Name: order_tools
          Config:
            AgentCoreGateway:
              GatewayArn: !GetAtt Gateway.GatewayArn
              # OutboundAuth omitted -> defaults to AWS_IAM (SigV4), which is what we want
      # Environment omitted -> AgentCore runs it in a managed microVM by default
      MaxIterations: 15
      TimeoutSeconds: 300
```

### 3d. New parameter

```yaml
  ModelId:
    Type: String
    Description: >-
      Bedrock model id or inference-profile id the agent loop uses, e.g. an
      Anthropic Claude inference profile available in your account/region.
      Model access must be enabled in the Bedrock console first.
```

---

## 4. Resource reference: `AWS::BedrockAgentCore::Harness`

| Property | Required | Type / notes |
|---|---|---|
| `HarnessName` | **Yes** | `^[a-zA-Z][a-zA-Z0-9_]{0,39}$`. Changing it **replaces** the resource. |
| `Model` | **Yes** | `HarnessModelConfiguration` (one of `BedrockModelConfig` / `OpenAiModelConfig` / `GeminiModelConfig` / `LiteLlmModelConfig`) |
| `ExecutionRoleArn` | **Yes** | IAM role the harness assumes when running |
| `Tools` | No | array of `HarnessTool` |
| `AllowedTools` | No | array of string; whitelist by name (all allowed by default), 1–64 |
| `SystemPrompt` | No | array of `HarnessSystemContentBlock` (e.g. `Text`) |
| `Skills` | No | array of `HarnessSkill` |
| `Memory` | No | `HarnessMemoryConfiguration` (AgentCore Memory for short/long-term) |
| `Environment` | No | `HarnessEnvironmentProvider` → `AgentCoreRuntimeEnvironment`. Omit for a managed microVM. |
| `EnvironmentArtifact` | No | container image the harness operates in |
| `EnvironmentVariables` | No | map of string→string |
| `MaxIterations` | No | int — max agent-loop iterations per invocation |
| `MaxTokens` | No | int — max total output tokens across all model calls per invocation |
| `TimeoutSeconds` | No | int — max duration per invocation |
| `Truncation` | No | `HarnessTruncationConfiguration` — context truncation |
| `AuthorizerConfiguration` | No | **inbound** auth for who may call the *harness* (see §6) |
| `Tags` | No | array of `Tag` |

### `HarnessTool`

| Property | Required | Notes |
|---|---|---|
| `Type` | **Yes** | `remote_mcp` \| `agentcore_browser` \| `agentcore_gateway` \| `inline_function` \| `agentcore_code_interpreter` |
| `Name` | No | `^[a-zA-Z0-9_-]+$`, 1–64; inferred if omitted |
| `Config` | No | `HarnessToolConfiguration` — a union; set the member matching `Type` |

### `HarnessToolConfiguration` (union — pick the one matching `Type`)

`AgentCoreGateway` | `RemoteMcp` | `InlineFunction` | `AgentCoreBrowser` | `AgentCoreCodeInterpreter`

### `HarnessAgentCoreGatewayConfig` (what we use)

| Property | Required | Notes |
|---|---|---|
| `GatewayArn` | **Yes** | ARN of the gateway. Pattern: `arn:aws:bedrock-agentcore:<region>:<acct>:gateway/<name>-<10char>` |
| `OutboundAuth` | No | `HarnessGatewayOutboundAuth`. **Defaults to `AWS_IAM` (SigV4)** — leave unset for IAM auth. |

### `HarnessModelConfiguration` → `HarnessBedrockModelConfig`

| Property | Required | Notes |
|---|---|---|
| `ModelId` | **Yes** | Bedrock model id / inference profile |
| `ApiFormat` | No | `converse_stream` \| `responses` \| `chat_completions` |
| `MaxTokens` | No | int ≥ 1 |
| `Temperature` | No | 0–2 |
| `TopP` | No | 0–1 |
| `AdditionalParams` | No | passthrough map to the provider |

---

## 5. Return values (`Fn::GetAtt`)

| Attribute | Meaning |
|---|---|
| `Arn` | ARN of the harness |
| `HarnessId` | id of the harness |
| `Status` | provisioning status |
| `Version` | incremented on every successful update |
| `CreatedAt` / `UpdatedAt` | timestamps |
| `Environment.AgentCoreRuntimeEnvironment.AgentRuntimeArn` / `...Id` / `...Name` | the underlying AgentCore Runtime the harness runs on |
| `Memory.ManagedMemoryConfiguration.Arn` | managed memory ARN (if memory configured) |

Add an output so the invoker knows the ARN:

```yaml
Outputs:
  HarnessArn:
    Value: !GetAtt AgentHarness.Arn
  HarnessId:
    Value: !GetAtt AgentHarness.HarnessId
```

---

## 6. Two separate auth boundaries (don't confuse them)

- **Inbound to the Harness** (`Harness.AuthorizerConfiguration`) — who may call the
  *agent*. Omit it and the harness is invoked via the AgentCore API using SigV4
  (the caller needs the AgentCore invoke permission on the harness ARN). You can
  also set a `CUSTOM_JWT` authorizer here if end users should call it with a
  token — independent of how the harness talks to the gateway.
- **Outbound Harness → Gateway** (`HarnessAgentCoreGatewayConfig.OutboundAuth`) —
  how the *agent* authenticates to the *gateway*. We use the default `AWS_IAM`,
  backed by the harness execution role + `bedrock-agentcore:InvokeGateway`.

This doc sets the second one to IAM. The first can stay IAM too (SigV4 caller).

---

## 7. Gotchas / verify before shipping

1. **`AuthorizerType` is one value per gateway.** Switching to `AWS_IAM` disables
   the JWT/Cognito path on that gateway. If some callers still need JWT, they
   can't share this gateway — use a separate gateway or reconsider.
2. **Enable Bedrock model access.** The `ModelId` must be a model your account
   has access to in the region; enable it in the Bedrock console first, and
   scope the role's `bedrock:InvokeModel*` resource to that model/inference-profile
   ARN instead of `*`.
3. **Region availability.** `AWS::BedrockAgentCore::Harness` is new — confirm the
   deploy region supports it (same regions as the other AgentCore CFN types).
4. **Confirm these two names against your provider version** (they're stable in
   the current CFN reference but worth a `describe`): the invoke action on the
   harness (for the harness's own inbound IAM auth) and the exact
   `bedrock:Converse*` action set your `ApiFormat` needs.
5. **microVM default.** Omitting `Environment` gives a managed microVM. Only set
   `Environment.AgentCoreRuntimeEnvironment` if you must pin it to a specific
   AgentCore Runtime.

---

## 8. References

- `AWS::BedrockAgentCore::Harness` (CFN): https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-harness.html
- Bedrock AgentCore CFN resource list: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/AWS_BedrockAgentCore.html
- Build agents with AgentCore Harness: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html
- IAM-based inbound auth for gateways (`bedrock-agentcore:InvokeGateway`): https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html
