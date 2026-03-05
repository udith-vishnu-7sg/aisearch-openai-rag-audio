#!/usr/bin/env python3
"""
Deploy Azure AI Foundry models using Python SDK.
This script provides an alternative to the bash script for deploying models.
"""

import os
import sys
import subprocess
from pathlib import Path

try:
    from azure.identity import DefaultAzureCredential, AzureDeveloperCliCredential
    from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
    from azure.mgmt.cognitiveservices.models import (
        Deployment,
        DeploymentProperties,
        DeploymentModel,
        Sku,
    )
except ImportError:
    print("ERROR: Required Azure packages not installed.")
    print("Install with: pip install azure-identity azure-mgmt-cognitiveservices")
    sys.exit(1)


def get_azd_env_value(key: str) -> str:
    """Get environment variable from azd."""
    try:
        result = subprocess.run(
            ["azd", "env", "get-value", key],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def load_env_from_file():
    """Load environment variables from azd .env file."""
    env_files = [
        Path(".azure/.env"),
        Path(f".azure/{os.environ.get('AZURE_ENV_NAME', 'default')}/.env"),
    ]
    
    for env_file in env_files:
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ[key] = value.strip('"')
            break


def get_service_info():
    """Get OpenAI service name and resource group."""
    # Load from azd
    endpoint = get_azd_env_value("AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    resource_group = get_azd_env_value("AZURE_RESOURCE_GROUP") or os.environ.get("AZURE_RESOURCE_GROUP", "")
    
    # Extract service name from endpoint
    service_name = ""
    if endpoint:
        # Extract from https://myservice.openai.azure.com
        parts = endpoint.replace("https://", "").replace("http://", "").split(".")
        if parts:
            service_name = parts[0]
    
    # Try to find resource group via Azure CLI if missing
    if not resource_group and service_name:
        try:
            result = subprocess.run(
                ["az", "cognitiveservices", "account", "show",
                 "--name", service_name,
                 "--query", "resourceGroup",
                 "-o", "tsv"],
                capture_output=True,
                text=True,
                check=True,
            )
            resource_group = result.stdout.strip()
        except subprocess.CalledProcessError:
            pass
    
    if not service_name:
        service_name = input("Enter OpenAI Service Name: ").strip()
    if not resource_group:
        resource_group = input("Enter Resource Group Name: ").strip()
    
    return service_name, resource_group


def deploy_model(client, resource_group: str, service_name: str, deployment_name: str, model_name: str, sku_name: str = "Standard", capacity: int = 1):
    """Deploy a model using Azure AI Foundry."""
    print(f"Checking if deployment '{deployment_name}' already exists...")
    
    try:
        # Check if deployment exists
        existing = client.deployments.get(
            resource_group_name=resource_group,
            account_name=service_name,
            deployment_name=deployment_name,
        )
        print(f"  ✓ Deployment '{deployment_name}' already exists, skipping...")
        return True
    except Exception:
        pass  # Deployment doesn't exist, continue
    
    print(f"  Deploying '{deployment_name}' (model: {model_name})...")
    
    # Create deployment model
    model = DeploymentModel(
        format="OpenAI",
        name=model_name,
        version="1",
    )
    
    # Create deployment properties
    deployment_properties = DeploymentProperties(
        model=model,
    )
    
    # Create SKU
    sku = Sku(
        name=sku_name,
        capacity=capacity,
    )
    
    # Create deployment
    deployment = Deployment(
        properties=deployment_properties,
        sku=sku,
    )
    
    try:
        client.deployments.begin_create_or_update(
            resource_group_name=resource_group,
            account_name=service_name,
            deployment_name=deployment_name,
            deployment=deployment,
        ).wait()
        print(f"  ✓ Successfully deployed '{deployment_name}'")
        return True
    except Exception as e:
        print(f"  ✗ Failed to deploy '{deployment_name}': {e}")
        return False


def main():
    """Main deployment function."""
    print("=== Azure AI Foundry Model Deployment (Python) ===\n")
    
    # Load environment variables
    load_env_from_file()
    
    # Get service information
    service_name, resource_group = get_service_info()
    
    if not service_name or not resource_group:
        print("ERROR: Could not determine service name or resource group")
        sys.exit(1)
    
    print(f"Deploying to service: {service_name} in resource group: {resource_group}\n")
    
    # Initialize Azure client
    try:
        credential = AzureDeveloperCliCredential() if os.environ.get("AZURE_TENANT_ID") else DefaultAzureCredential()
        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID") or subprocess.run(
            ["az", "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        
        client = CognitiveServicesManagementClient(credential, subscription_id)
    except Exception as e:
        print(f"ERROR: Failed to initialize Azure client: {e}")
        print("Make sure you're logged in: az login")
        sys.exit(1)
    
    # Deploy models
    results = []
    
    print("=== Deploying gpt-realtime-mini (transcription and text-to-speech) ===")
    results.append(deploy_model(client, resource_group, service_name, "gpt-realtime-mini", "gpt-realtime-mini", sku_name="GlobalStandard"))
    
    print("\n=== Deploying gpt-5-mini (chat) ===")
    results.append(deploy_model(client, resource_group, service_name, "gpt-5-mini", "gpt-5-mini", sku_name="GlobalStandard"))
    
    print("\n=== Deploying text-embedding-3-large (embeddings) ===")
    results.append(deploy_model(client, resource_group, service_name, "text-embedding-3-large", "text-embedding-3-large", sku_name="Standard", capacity=30))
    
    # Summary
    print("\n=== Deployment Summary ===")
    success_count = sum(results)
    
    if success_count == 3:
        print("✓ All models deployed successfully!")
        print("\nYou can now use the application.")
    else:
        print(f"⚠ Some deployments may have failed ({success_count}/3 succeeded).")
        print("Please check the errors above and deploy manually via Azure AI Foundry Portal if needed.")
        print("See docs/manual_model_deployment.md for instructions.")
    
    # List current deployments
    print("\nCurrent deployments:")
    try:
        deployments = client.deployments.list(
            resource_group_name=resource_group,
            account_name=service_name,
        )
        for deployment in deployments:
            print(f"  - {deployment.name}: {deployment.properties.model.name}")
    except Exception as e:
        print(f"  Could not list deployments: {e}")


if __name__ == "__main__":
    main()

