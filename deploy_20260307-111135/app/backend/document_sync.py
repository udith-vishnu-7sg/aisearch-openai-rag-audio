"""Periodic document synchronization from local /data folder to Azure Blob Storage."""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexerClient
from azure.storage.blob import BlobServiceClient

logger = logging.getLogger("voicerag")


class DocumentSync:
    """Handles periodic synchronization of documents from local /data folder to blob storage."""
    
    def __init__(
        self,
        storage_endpoint: str,
        storage_container: str,
        search_endpoint: str,
        indexer_name: str,
        credential: AzureKeyCredential | AzureDeveloperCliCredential | DefaultAzureCredential,
        data_folder: str = "data",
        sync_interval_seconds: int = 300  # Default: 5 minutes
    ):
        """
        Initialize document sync.
        
        Args:
            storage_endpoint: Azure Storage account URL
            storage_container: Container name for documents
            search_endpoint: Azure Search endpoint
            indexer_name: Name of the search indexer to trigger
            credential: Azure credential for authentication
            data_folder: Local folder path containing documents (relative to project root)
            sync_interval_seconds: How often to check for new documents (in seconds)
        """
        self.storage_endpoint = storage_endpoint
        self.storage_container = storage_container
        self.search_endpoint = search_endpoint
        self.indexer_name = indexer_name
        self.credential = credential
        self.data_folder = Path(data_folder)
        self.sync_interval_seconds = sync_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Get project root (assuming this file is in app/backend/)
        project_root = Path(__file__).parent.parent.parent
        self.data_path = project_root / self.data_folder
        
        # Initialize clients
        self.blob_client = BlobServiceClient(
            account_url=storage_endpoint,
            credential=credential,
            max_single_put_size=4 * 1024 * 1024
        )
        self.indexer_client = SearchIndexerClient(search_endpoint, credential)
    
    async def sync_documents(self) -> None:
        """Sync documents from local /data folder to blob storage."""
        try:
            if not self.data_path.exists():
                logger.debug(f"Data folder does not exist: {self.data_path} (this is normal in production)")
                return
            
            if not self.data_path.is_dir():
                logger.warning(f"Data path exists but is not a directory: {self.data_path}")
                return
            
            container_client = self.blob_client.get_container_client(self.storage_container)
            if not container_client.exists():
                logger.info(f"Creating container: {self.storage_container}")
                container_client.create_container()
            
            # Get list of existing blobs
            existing_blobs = {blob.name: blob.last_modified for blob in container_client.list_blobs()}
            
            # Track if any new files were uploaded
            new_files_uploaded = False
            
            # Check each file in /data folder
            for file_path in self.data_path.iterdir():
                if file_path.is_file() and not file_path.name.startswith('.'):
                    filename = file_path.name
                    local_mtime = file_path.stat().st_mtime
                    
                    # Check if blob exists and if local file is newer
                    should_upload = False
                    if filename not in existing_blobs:
                        logger.info(f"New file detected, uploading: {filename}")
                        should_upload = True
                    else:
                        # Compare modification times (blob last_modified is timezone-aware datetime)
                        blob_mtime = existing_blobs[filename].timestamp()
                        if local_mtime > blob_mtime:
                            logger.info(f"File updated locally, re-uploading: {filename}")
                            should_upload = True
                    
                    if should_upload:
                        try:
                            with open(file_path, "rb") as opened_file:
                                container_client.upload_blob(
                                    filename,
                                    opened_file,
                                    overwrite=True
                                )
                            logger.info(f"Successfully uploaded: {filename}")
                            new_files_uploaded = True
                        except Exception as e:
                            logger.error(f"Error uploading {filename}: {e}")
            
            # Trigger indexer if new files were uploaded
            if new_files_uploaded:
                try:
                    logger.info(f"Triggering indexer: {self.indexer_name}")
                    self.indexer_client.run_indexer(self.indexer_name)
                    logger.info("Indexer triggered successfully")
                except Exception as e:
                    logger.error(f"Error triggering indexer: {e}")
            else:
                logger.debug("No new or updated files to sync")
                
        except Exception as e:
            logger.error(f"Error during document sync: {e}", exc_info=True)
    
    async def _sync_loop(self) -> None:
        """Background task loop for periodic syncing."""
        logger.info(f"Document sync started (checking every {self.sync_interval_seconds} seconds)")
        while self._running:
            try:
                await self.sync_documents()
            except Exception as e:
                logger.error(f"Error in sync loop: {e}", exc_info=True)
            
            # Wait for next sync interval
            await asyncio.sleep(self.sync_interval_seconds)
    
    def start(self) -> None:
        """Start the periodic sync task."""
        if self._running:
            logger.warning("Document sync is already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._sync_loop())
        logger.info("Document sync background task started")
    
    def stop(self) -> None:
        """Stop the periodic sync task."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Document sync background task stopped")
    
    async def sync_once(self) -> None:
        """Perform a one-time sync (useful for manual triggers or initial sync)."""
        await self.sync_documents()
