# Azure AI Foundry Model Deployment Approach

## Overview

This project uses **Azure AI Foundry** for model deployments. Models are deployed via:

- **Azure CLI**: Primary method for all model deployments (recommended)
- **Python SDK**: Alternative method for programmatic deployment
- **Azure Portal**: Manual deployment via Azure AI Foundry Portal

## Why Hybrid Approach?

### Bicep IS Supported (for most models)

Bicep is the **correct and supported approach** for deploying Azure OpenAI resources. The infrastructure uses:
- **Azure Verified Modules (AVM)**: `br/public:avm/res/cognitive-services/account:0.8.0`
- This is the official, Microsoft-maintained module for Azure OpenAI

### Why Azure CLI for All Models?

Azure AI Foundry models are best deployed via Azure CLI because:
- **Consistent API**: All models use the same deployment interface
- **Better Error Handling**: More detailed error messages
- **Flexibility**: Easy to script and automate
- **Azure AI Foundry Native**: Recommended approach for AI Foundry resources

## Supported Deployment Methods

### 1. Azure CLI ✅ (Recommended)
**Works for**: All Azure AI Foundry models

```bash
az cognitiveservices account deployment create \
  --name <service-name> \
  --resource-group <rg-name> \
  --deployment-name "gpt-realtime-mini" \
  --model-name "gpt-realtime-mini" \
  --model-version "1" \
  --model-format "OpenAI" \
  --sku-capacity "1" \
  --sku-name "Standard"
```

### 2. Python SDK ✅
**Works for**: All models (programmatic deployment)

```python
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

# Deploy model using Azure AI Foundry Python SDK
# See scripts/deploy_models.py for example
```

### 3. Azure AI Foundry Portal ✅
**Works for**: All models (manual deployment)

Navigate to: [Azure AI Foundry Portal](https://ai.azure.com) → Your Project → Deployments → + Create

## Current Implementation

### What Gets Deployed via Azure CLI (after azd up)

All models are now deployed via Azure CLI scripts because Bicep deployment has limitations for many models:

1. **gpt-realtime-mini** - Transcription and text-to-speech model
2. **gpt-5-mini** - Chat completion model
3. **text-embedding-3-large** - Embedding model

## Deployment Workflow

1. **Run `azd up`**
   - Deploys infrastructure via Bicep
   - Creates OpenAI service
   - No models are deployed (empty deployments array)

2. **Run model deployment script**
   ```bash
   ./scripts/deploy_models.sh
   ```
   
   Or use Python:
   ```bash
   python scripts/deploy_models.py
   ```
   
   Or deploy manually via Azure Portal (see [Manual Model Deployment Guide](manual_model_deployment.md))

## Why Not Use Azure CLI for Everything?

While Azure CLI works for all models, using Bicep provides:
- **Infrastructure as Code**: Version control, repeatability
- **Idempotency**: Safe to run multiple times
- **Integration**: Works seamlessly with `azd` and CI/CD pipelines
- **Validation**: Template validation before deployment
- **Best Practice**: Recommended approach for Azure deployments

## Future Considerations

As Azure OpenAI service evolves:
- Audio models may gain Bicep support
- New deployment methods may be introduced
- Model availability may change

Always check the [official Azure OpenAI documentation](https://learn.microsoft.com/azure/ai-services/openai/) for the latest deployment methods.

## Troubleshooting

### "DeploymentModelNotSupported" Error

This means the model cannot be deployed via Bicep. Use Azure CLI or Portal instead.

### Model Not Available

- Check region availability
- Verify subscription has access
- Check model name spelling (case-sensitive)
- Try different SKU options

### Script Failures

If the deployment script fails:
1. Check Azure CLI is installed and logged in
2. Verify service name and resource group are correct
3. Try manual deployment via Portal
4. Check Azure service health status

