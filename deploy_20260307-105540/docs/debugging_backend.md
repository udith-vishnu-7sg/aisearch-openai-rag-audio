# Debugging the Backend

When debugging issues with the deployed backend in Azure Container Apps, here are the best methods:

## Method 1: Azure Portal Log Stream (Easiest)

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to your Container App (search for "Container Apps" in the top search bar)
3. Find your backend container app (usually named something like `capps-backend-...`)
4. In the left sidebar, click on **"Log stream"** under the "Monitoring" section
5. You'll see real-time logs from your application

**Tip**: The logs will show all the `[RTMiddleTier]`, `[App]`, and other prefixed messages we added for debugging.

## Method 2: Azure CLI Log Streaming

Stream logs directly to your terminal:

```bash
# Get your container app name
az containerapp list --query "[?contains(name, 'backend')].{name:name, resourceGroup:resourceGroup}" -o table

# Stream logs (replace with your actual app name and resource group)
az containerapp logs show \
  --name <your-backend-app-name> \
  --resource-group <your-resource-group> \
  --follow
```

Or use the provided script:

```bash
./scripts/debug_backend.sh
```

This script automatically finds your resource group and container app name using `azd`.

## Method 3: View Recent Logs (Not Streaming)

View the last N lines of logs:

```bash
az containerapp logs show \
  --name <your-backend-app-name> \
  --resource-group <your-resource-group> \
  --tail 100
```

## Method 4: Log Analytics Workspace (Advanced)

For more detailed querying and analysis:

1. In Azure Portal, go to your Container App
2. Click on **"Logs"** under the "Monitoring" section
3. This opens Log Analytics with KQL (Kusto Query Language)
4. Try queries like:
   ```kusto
   ContainerAppConsoleLogs_CL
   | where TimeGenerated > ago(1h)
   | where Log_s contains "RTMiddleTier" or Log_s contains "App"
   | project TimeGenerated, Log_s
   | order by TimeGenerated desc
   ```

## Method 5: Check Container App Status

Verify the container is running:

```bash
az containerapp show \
  --name <your-backend-app-name> \
  --resource-group <your-resource-group> \
  --query "properties.runningStatus"
```

## Common Issues and What to Look For

### 500 Internal Server Error

When you see a 500 error, check the logs for:

1. **Model deployment not found**:
   - Look for: `"Model deployment 'gpt-realtime-mini' not found"`
   - **Fix**: Run `./scripts/deploy_models.sh` to deploy the models

2. **Authentication errors**:
   - Look for: `"Authentication failed"` or `"unauthorized"`
   - **Fix**: Check that managed identity has proper permissions, or API keys are set correctly

3. **Audio conversion errors**:
   - Look for: `"Error converting PCM to WAV"`
   - **Fix**: Check audio format being sent from frontend

4. **API version errors**:
   - Look for: `"API version"` or `"unsupported"`
   - **Fix**: Check `api_version` in `rtmt.py` matches your Azure OpenAI service

### Environment Variables

Verify environment variables are set correctly:

```bash
az containerapp show \
  --name <your-backend-app-name> \
  --resource-group <your-resource-group> \
  --query "properties.template.containers[0].env"
```

## Testing Locally

If you want to test the backend locally with the same Azure services:

1. Make sure you have `app/backend/.env` file (run `./scripts/write_env.sh` after `azd up`)
2. Start the backend locally:
   ```bash
   cd app/backend
   python app.py
   ```
3. The logs will appear directly in your terminal

## Improving Error Messages

The backend now returns detailed error messages in the JSON response:

```json
{
  "error": "Transcription failed: Model deployment 'gpt-realtime-mini' not found",
  "error_type": "HTTPInternalServerError",
  "details": "..."
}
```

Check the browser console Network tab to see the full error response.

