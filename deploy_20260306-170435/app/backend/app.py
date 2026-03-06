import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from dotenv import load_dotenv

from document_sync import DocumentSync
from ragtools import attach_rag_tools
from rtmt import RTMiddleTier

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("voicerag")

async def create_app():
    logger.info("[App] Initializing application...")
    if not os.environ.get("RUNNING_IN_PRODUCTION"):
        logger.info("[App] Running in development mode, loading from .env file")
        load_dotenv()
    else:
        logger.info("[App] Running in production mode")

    llm_key = os.environ.get("AZURE_OPENAI_API_KEY")
    search_key = os.environ.get("AZURE_SEARCH_API_KEY")

    credential = None
    if not llm_key or not search_key:
        if tenant_id := os.environ.get("AZURE_TENANT_ID"):
            logger.info("Using AzureDeveloperCliCredential with tenant_id %s", tenant_id)
            credential = AzureDeveloperCliCredential(tenant_id=tenant_id, process_timeout=60)
        else:
            logger.info("Using DefaultAzureCredential")
            credential = DefaultAzureCredential()
    llm_credential = AzureKeyCredential(llm_key) if llm_key else credential
    search_credential = AzureKeyCredential(search_key) if search_key else credential
    
    app = web.Application()

    # Get deployment name - gpt-realtime-mini for Realtime API (WebSocket)
    realtime_deployment = os.environ.get("AZURE_OPENAI_REALTIME_DEPLOYMENT", "gpt-realtime-mini")
    voice_choice = os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE") or "alloy"

    logger.info(f"[App] Initializing RTMiddleTier with endpoint: {os.environ['AZURE_OPENAI_ENDPOINT']}")
    logger.info(f"[App] Realtime deployment: {realtime_deployment}, Voice: {voice_choice}")
    rtmt = RTMiddleTier(
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        deployment=realtime_deployment,
        credentials=llm_credential,
        voice_choice=voice_choice
    )
    logger.info("[App] RTMiddleTier initialized successfully")
    
    rtmt.system_message = """
        You are a helpful assistant. Only answer questions based on information you searched in the knowledge base, accessible with the 'search' tool. 
        The user is listening to answers with audio, so it's *super* important that answers are as short as possible, a single sentence if at all possible. 
        Never read file names or source names or keys out loud. 
        Always use the following step-by-step instructions to respond: 
        1. Always use the 'search' tool to check the knowledge base before answering a question. 
        2. Always use the 'report_grounding' tool to report the source of information from the knowledge base. 
        3. Produce an answer that's as short as possible. If the answer isn't in the knowledge base, say you don't know.
    """.strip()

    attach_rag_tools(rtmt,
        credentials=search_credential,
        search_endpoint=os.environ.get("AZURE_SEARCH_ENDPOINT"),
        search_index=os.environ.get("AZURE_SEARCH_INDEX"),
        semantic_configuration=os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIGURATION") or None,
        identifier_field=os.environ.get("AZURE_SEARCH_IDENTIFIER_FIELD") or "chunk_id",
        content_field=os.environ.get("AZURE_SEARCH_CONTENT_FIELD") or "chunk",
        embedding_field=os.environ.get("AZURE_SEARCH_EMBEDDING_FIELD") or "text_vector",
        title_field=os.environ.get("AZURE_SEARCH_TITLE_FIELD") or "title",
        use_vector_query=(os.getenv("AZURE_SEARCH_USE_VECTOR_QUERY", "true") == "true")
        )

    logger.info("[App] Attaching WebSocket endpoint to app")
    rtmt.attach_to_app(app, "/realtime")

    current_directory = Path(__file__).parent
    app.add_routes([web.get('/', lambda _: web.FileResponse(current_directory / 'static/index.html'))])
    app.router.add_static('/', path=current_directory / 'static', name='static')
    
    # Initialize document sync if storage endpoint is configured
    doc_sync: DocumentSync | None = None
    if os.environ.get("AZURE_STORAGE_ENDPOINT") and os.environ.get("AZURE_SEARCH_INDEX"):
        try:
            sync_interval = int(os.environ.get("DOCUMENT_SYNC_INTERVAL_SECONDS", "300"))  # Default: 5 minutes
            doc_sync = DocumentSync(
                storage_endpoint=os.environ["AZURE_STORAGE_ENDPOINT"],
                storage_container=os.environ.get("AZURE_STORAGE_CONTAINER", "documents"),
                search_endpoint=os.environ.get("AZURE_SEARCH_ENDPOINT"),
                indexer_name=os.environ.get("AZURE_SEARCH_INDEX"),
                credential=search_credential,
                sync_interval_seconds=sync_interval
            )
            logger.info(f"[App] Document sync initialized (interval: {sync_interval}s)")
        except Exception as e:
            logger.warning(f"[App] Failed to initialize document sync: {e}")
            doc_sync = None
    
    # Store doc_sync in app for cleanup
    app["doc_sync"] = doc_sync
    
    # Start document sync background task
    async def on_startup(app):
        sync = app.get("doc_sync")
        if sync:
            sync.start()
            logger.info("[App] Document sync started")
    
    async def on_cleanup(app):
        sync = app.get("doc_sync")
        if sync:
            sync.stop()
            logger.info("[App] Document sync stopped")
    
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    
    logger.info("[App] Application initialization complete")
    return app

if __name__ == "__main__":
    host = "localhost"
    port = 8765
    web.run_app(create_app(), host=host, port=port)
