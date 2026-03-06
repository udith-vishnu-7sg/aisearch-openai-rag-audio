#!/bin/bash
# Script to deploy all Azure OpenAI models via Azure AI Foundry using Azure CLI
# This should be run after azd up completes
# Deploys: gpt-realtime-mini (transcription & TTS), gpt-5-mini (chat), and embeddings

set -e

echo "=== Azure AI Foundry Model Deployment ==="
echo ""

# Load environment variables from azd
# Try multiple locations for azd environment variables
if [ -f ".azure/.env" ]; then
    export $(cat .azure/.env | grep -v '^#' | xargs)
elif [ -f ".azure/${AZURE_ENV_NAME:-default}/.env" ]; then
    export $(cat ".azure/${AZURE_ENV_NAME:-default}/.env" | grep -v '^#' | xargs)
fi

# Also try to get from azd directly
if command -v azd &> /dev/null; then
    # Get OpenAI endpoint and extract service name
    if [ -z "$AZURE_OPENAI_ENDPOINT" ]; then
        AZURE_OPENAI_ENDPOINT=$(azd env get-value AZURE_OPENAI_ENDPOINT 2>/dev/null || echo "")
    fi
    
    # Extract service name from endpoint (e.g., https://myservice.openai.azure.com -> myservice)
    if [ -n "$AZURE_OPENAI_ENDPOINT" ] && [ -z "$AZURE_OPENAI_SERVICE" ]; then
        AZURE_OPENAI_SERVICE=$(echo "$AZURE_OPENAI_ENDPOINT" | sed -E 's|https?://([^.]+)\..*|\1|')
    fi
    
    # Get resource group from azd
    if [ -z "$AZURE_OPENAI_RESOURCE_GROUP" ]; then
        # Try to get AZURE_RESOURCE_GROUP from azd (this is the actual resource group name)
        AZURE_OPENAI_RESOURCE_GROUP=$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || echo "")
        # If that's not set, try using the environment name (fallback)
        if [ -z "$AZURE_OPENAI_RESOURCE_GROUP" ]; then
            AZURE_ENV_NAME=$(azd env get-value AZURE_ENV_NAME 2>/dev/null || echo "")
            if [ -n "$AZURE_ENV_NAME" ]; then
                # Resource group might be the environment name with rg- prefix
                AZURE_OPENAI_RESOURCE_GROUP="rg-${AZURE_ENV_NAME}"
            fi
        fi
    fi
fi

# Get OpenAI service details
OPENAI_SERVICE=${AZURE_OPENAI_SERVICE:-""}
OPENAI_RG=${AZURE_OPENAI_RESOURCE_GROUP:-""}

# If service name is missing, try to extract from endpoint
if [ -z "$OPENAI_SERVICE" ] && [ -n "$AZURE_OPENAI_ENDPOINT" ]; then
    OPENAI_SERVICE=$(echo "$AZURE_OPENAI_ENDPOINT" | sed -E 's|https?://([^.]+)\..*|\1|')
fi

# If resource group is missing, try to find it using Azure CLI
if [ -z "$OPENAI_RG" ] && [ -n "$OPENAI_SERVICE" ]; then
    echo "Attempting to find resource group for OpenAI service '$OPENAI_SERVICE'..."
    OPENAI_RG=$(az cognitiveservices account show --name "$OPENAI_SERVICE" --query "resourceGroup" -o tsv 2>/dev/null || echo "")
fi

if [ -z "$OPENAI_SERVICE" ] || [ -z "$OPENAI_RG" ]; then
    echo "ERROR: Could not determine OpenAI service name or resource group"
    echo ""
    if [ -z "$OPENAI_SERVICE" ]; then
        echo "OpenAI Service Name:"
        echo "  - Extract from endpoint: https://<SERVICE_NAME>.openai.azure.com"
        echo "  - Or find in Azure Portal: Azure AI Foundry > Your service"
        read -p "Enter OpenAI Service Name: " OPENAI_SERVICE
    fi
    if [ -z "$OPENAI_RG" ]; then
        echo ""
        echo "Resource Group Name:"
        echo "  - Get from azd: azd env get-value AZURE_RESOURCE_GROUP"
        echo "  - Usually 'rg-<environment-name>' (e.g., rg-my-voice-rag-env)"
        echo "  - Or find in Azure Portal: Azure AI Foundry > Your service > Resource group"
        read -p "Enter Resource Group Name: " OPENAI_RG
    fi
fi

echo "Deploying to service: $OPENAI_SERVICE in resource group: $OPENAI_RG"
echo ""

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    echo "ERROR: Azure CLI not found. Please install it first."
    exit 1
fi

# Check if logged in
if ! az account show &> /dev/null; then
    echo "ERROR: Not logged in to Azure. Run 'az login' first."
    exit 1
fi

# Function to deploy a model
deploy_model() {
    local deployment_name=$1
    local model_name=$2
    local sku_name=${3:-"Standard"}
    local capacity=${4:-1}
    
    echo "Checking if deployment '$deployment_name' already exists..."
    
    # Check if deployment exists
    if az cognitiveservices account deployment show \
        --name "$OPENAI_SERVICE" \
        --resource-group "$OPENAI_RG" \
        --deployment-name "$deployment_name" &> /dev/null; then
        echo "  ✓ Deployment '$deployment_name' already exists, skipping..."
        return 0
    fi
    
    echo "  Deploying '$deployment_name' (model: $model_name)..."
    
    # Try to deploy with specified SKU
    if az cognitiveservices account deployment create \
        --name "$OPENAI_SERVICE" \
        --resource-group "$OPENAI_RG" \
        --deployment-name "$deployment_name" \
        --model-name "$model_name" \
        --model-version "1" \
        --model-format "OpenAI" \
        --sku-capacity "$capacity" \
        --sku-name "$sku_name" \
        --output none 2>&1; then
        echo "  ✓ Successfully deployed '$deployment_name'"
        return 0
    else
        echo "  ✗ Failed to deploy '$deployment_name' with $sku_name SKU"
        echo "  Please check the error above and deploy manually via Azure AI Foundry Portal if needed"
        return 1
    fi
}

# Deploy gpt-realtime-mini (transcription)
echo "=== Deploying gpt-realtime-mini (transcription) ==="
deploy_model "gpt-realtime-mini" "gpt-realtime-mini" "GlobalStandard"
REALTIME_RESULT=$?

echo ""

# Deploy tts-hd (text-to-speech for /synthesize endpoint - required for profile data generation)
echo "=== Deploying tts-hd (text-to-speech) ==="
deploy_model "tts-hd" "tts-hd" "Standard"
TTS_RESULT=$?

echo ""

# Deploy gpt-5-mini (chat)
echo "=== Deploying gpt-5-mini (chat) ==="
deploy_model "gpt-5-mini" "gpt-5-mini" "GlobalStandard"
CHAT_RESULT=$?

echo ""

# Deploy embeddings
echo "=== Deploying text-embedding-3-large (embeddings) ==="
deploy_model "text-embedding-3-large" "text-embedding-3-large" "Standard" 30
EMBED_RESULT=$?

echo ""
echo "=== Deployment Summary ==="
SUCCESS_COUNT=0
if [ $REALTIME_RESULT -eq 0 ]; then ((SUCCESS_COUNT++)); fi
if [ $TTS_RESULT -eq 0 ]; then ((SUCCESS_COUNT++)); fi
if [ $CHAT_RESULT -eq 0 ]; then ((SUCCESS_COUNT++)); fi
if [ $EMBED_RESULT -eq 0 ]; then ((SUCCESS_COUNT++)); fi

if [ $SUCCESS_COUNT -eq 4 ]; then
    echo "✓ All models deployed successfully!"
    echo ""
    echo "You can now use the application."
else
    echo "⚠ Some deployments may have failed ($SUCCESS_COUNT/4 succeeded)."
    echo "Please check the errors above and deploy manually via Azure Portal if needed."
    echo "See docs/manual_model_deployment.md for instructions."
fi

echo ""
echo "Current deployments:"
az cognitiveservices account deployment list \
    --name "$OPENAI_SERVICE" \
    --resource-group "$OPENAI_RG" \
    --output table

