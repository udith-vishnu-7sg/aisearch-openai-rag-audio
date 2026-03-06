#!/bin/bash

# Get resource group from azd
RESOURCE_GROUP=$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || echo "")

if [ -z "$RESOURCE_GROUP" ]; then
    echo "Error: Could not find AZURE_RESOURCE_GROUP. Make sure you're in an azd environment."
    exit 1
fi

# Get container app name
APP_NAME=$(az containerapp list --resource-group "$RESOURCE_GROUP" --query "[?contains(name, 'backend')].name" -o tsv | head -1)

if [ -z "$APP_NAME" ]; then
    echo "Error: Could not find backend container app in resource group $RESOURCE_GROUP"
    exit 1
fi

echo "Streaming logs for: $APP_NAME"
echo "Resource Group: $RESOURCE_GROUP"
echo "Press Ctrl+C to stop"
echo ""

az containerapp logs show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --follow

