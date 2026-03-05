# Manual Model Deployment Guide

Azure AI Foundry models can be deployed via Azure CLI, Python SDK, or the Azure Portal. This guide explains how to deploy models after running `azd up`.

## Models Requiring Manual Deployment

All models need to be deployed manually (or via the provided scripts) because Bicep deployment is not supported for most models:

- **gpt-realtime-mini** - For audio transcription and text-to-speech synthesis
- **gpt-5-mini** - For chat completions
- **text-embedding-3-large** - For embeddings

## Deployment Steps

After running `azd up` and the infrastructure is provisioned:

1. **Navigate to Azure AI Foundry**
   - Go to [Azure AI Foundry Portal](https://ai.azure.com) or [Azure Portal](https://portal.azure.com)
   - Find your Azure OpenAI resource (created by `azd up`)

2. **Deploy gpt-realtime-mini (transcription and TTS)**
   - In your OpenAI resource, go to **"Deployments"** or **"Model deployments"**
   - Click **"+ Create"** or **"+ Add deployment"**
   - Configure:
     - **Deployment name**: `gpt-realtime-mini`
     - **Model**: `gpt-realtime-mini`
     - **Model version**: Select the latest available version (typically `1`)
     - **Deployment type/SKU**: **Global Standard** (SKU name: `GlobalStandard`)
     - **Capacity**: 1 (or as needed)
   - Click **"Create"**
   - **Note**: This single deployment is used for both transcription and text-to-speech

3. **Deploy gpt-5-mini (chat)**
   - In the same Deployments section, click **"+ Create"** again
   - Configure:
     - **Deployment name**: `gpt-5-mini`
     - **Model**: `gpt-5-mini`
     - **Model version**: Select the latest available version (typically `1`)
     - **Deployment type/SKU**: **Global Standard** (SKU name: `GlobalStandard`)
     - **Capacity**: 1 (or as needed)
   - Click **"Create"**

4. **Deploy text-embedding-3-large (embeddings)**
   - In the same Deployments section, click **"+ Create"** again
   - Configure:
     - **Deployment name**: `text-embedding-3-large`
     - **Model**: `text-embedding-3-large`
     - **Model version**: Select the latest available version (typically `1`)
     - **Deployment type/SKU**: **Standard** (SKU name: `Standard`)
     - **Capacity**: 30 (or as needed)
   - Click **"Create"**

5. **Verify Deployments**
   - Ensure all deployments show as "Succeeded"
   - The deployment names must match exactly:
     - `gpt-realtime-mini`
     - `gpt-5-mini`
     - `text-embedding-3-large`

## Deployment via Azure CLI

Deploy models using Azure CLI (recommended for automation):

```bash
# Set variables
export OPENAI_SERVICE="<your-service-name>"
export RESOURCE_GROUP="<your-resource-group>"
```

```bash
# Set variables
OPENAI_SERVICE="<your-openai-service-name>"
RESOURCE_GROUP="<your-resource-group>"

# Deploy gpt-realtime-mini (transcription and TTS)
az cognitiveservices account deployment create \
  --name "$OPENAI_SERVICE" \
  --resource-group "$RESOURCE_GROUP" \
  --deployment-name "gpt-realtime-mini" \
  --model-name "gpt-realtime-mini" \
  --model-version "1" \
  --model-format "OpenAI" \
  --sku-capacity "1" \
  --sku-name "GlobalStandard"

# Deploy gpt-5-mini (chat)
az cognitiveservices account deployment create \
  --name "$OPENAI_SERVICE" \
  --resource-group "$RESOURCE_GROUP" \
  --deployment-name "gpt-5-mini" \
  --model-name "gpt-5-mini" \
  --model-version "1" \
  --model-format "OpenAI" \
  --sku-capacity "1" \
  --sku-name "GlobalStandard"

# Deploy text-embedding-3-large (embeddings)
az cognitiveservices account deployment create \
  --name "$OPENAI_SERVICE" \
  --resource-group "$RESOURCE_GROUP" \
  --deployment-name "text-embedding-3-large" \
  --model-name "text-embedding-3-large" \
  --model-version "1" \
  --model-format "OpenAI" \
  --sku-capacity "30" \
  --sku-name "Standard"
```

## Troubleshooting

### Model Not Available

If you don't see `gpt-realtime-mini` in the model list:

1. **Check Region Availability**: These models may not be available in all regions
2. **Check Subscription Access**: Your subscription may need approval for these models
3. **Check Model Names**: The exact model names may vary - check Azure documentation for current names

### Deployment Fails

If deployment fails:

1. Check that your subscription has quota for the models
2. Verify the model name is correct (case-sensitive)
3. Try a different SKU or capacity
4. Check Azure service health status

## Deployment via Python

Alternatively, deploy models using the Python script:

```bash
python scripts/deploy_models.py
```

This requires the Azure SDK packages:
```bash
pip install azure-identity azure-mgmt-cognitiveservices
```

## Verification

Verify deployments using Azure CLI or Python:

```bash
# Azure CLI
az cognitiveservices account deployment list \
  --name "$OPENAI_SERVICE" \
  --resource-group "$RESOURCE_GROUP" \
  --output table
```

