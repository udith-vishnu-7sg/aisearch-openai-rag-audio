import json
import logging
import os
import subprocess

from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureDeveloperCliCredential
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.search.documents.indexes.models import (
    AzureOpenAIEmbeddingSkill,
    AzureOpenAIParameters,
    AzureOpenAIVectorizer,
    FieldMapping,
    HnswAlgorithmConfiguration,
    HnswParameters,
    IndexProjectionMode,
    InputFieldMappingEntry,
    OutputFieldMappingEntry,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerDataSourceType,
    SearchIndexerIndexProjections,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerSkillset,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    SplitSkill,
    VectorSearch,
    VectorSearchAlgorithmMetric,
    VectorSearchProfile,
    IndexingSchedule,
)
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from rich.logging import RichHandler


def load_azd_env():
    """Get path to current azd env file and load file using python-dotenv"""
    result = subprocess.run("azd env list -o json", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception("Error loading azd env")
    env_json = json.loads(result.stdout)
    env_file_path = None
    for entry in env_json:
        if entry["IsDefault"]:
            env_file_path = entry["DotEnvPath"]
    if not env_file_path:
        raise Exception("No default azd env file found")
    logger.info(f"Loading azd env from {env_file_path}")
    load_dotenv(env_file_path, override=True)


def minutes_to_iso8601_duration(minutes: int) -> str:
    """Convert minutes to ISO 8601 duration format (e.g., PT60M for 60 minutes)."""
    if minutes < 5:
        minutes = 5  # Minimum interval is 5 minutes
    if minutes >= 1440:
        # For 24+ hours, use days
        days = minutes // 1440
        remaining_minutes = minutes % 1440
        if remaining_minutes == 0:
            return f"P{days}D"
        else:
            hours = remaining_minutes // 60
            mins = remaining_minutes % 60
            if mins == 0:
                return f"P{days}DT{hours}H"
            else:
                return f"P{days}DT{hours}H{mins}M"
    elif minutes >= 60:
        # For 1+ hours, use hours and minutes
        hours = minutes // 60
        mins = minutes % 60
        if mins == 0:
            return f"PT{hours}H"
        else:
            return f"PT{hours}H{mins}M"
    else:
        # For less than 1 hour, use minutes only
        return f"PT{minutes}M"


def setup_index(azure_credential, index_name, azure_search_endpoint, azure_storage_connection_string, azure_storage_container, azure_openai_embedding_endpoint, azure_openai_embedding_deployment, azure_openai_embedding_model, azure_openai_embeddings_dimensions):
    index_client = SearchIndexClient(azure_search_endpoint, azure_credential)
    indexer_client = SearchIndexerClient(azure_search_endpoint, azure_credential)

    data_source_connections = indexer_client.get_data_source_connections()
    if index_name in [ds.name for ds in data_source_connections]:
        logger.info(f"Data source connection {index_name} already exists, not re-creating")
    else:
        logger.info(f"Creating data source connection: {index_name}")
        indexer_client.create_data_source_connection(
            data_source_connection=SearchIndexerDataSourceConnection(
                name=index_name, 
                type=SearchIndexerDataSourceType.AZURE_BLOB,
                connection_string=azure_storage_connection_string,
                container=SearchIndexerDataContainer(name=azure_storage_container)))

    index_names = [index.name for index in index_client.list_indexes()]
    index_needs_update = False
    
    if index_name in index_names:
        logger.info(f"Index {index_name} already exists, checking if it needs updates...")
        # Check if index has required parent_id field
        existing_index = index_client.get_index(index_name)
        existing_field_names = [field.name for field in existing_index.fields]
        if "parent_id" not in existing_field_names:
            logger.warning(f"Index {index_name} is missing 'parent_id' field required for index projections. Index will be recreated.")
            # Delete the existing index so we can recreate it with the correct schema
            try:
                index_client.delete_index(index_name)
                logger.info(f"Deleted existing index {index_name}")
                index_needs_update = True
            except Exception as e:
                logger.error(f"Failed to delete index {index_name}: {e}")
                raise
        else:
            logger.info(f"Index {index_name} has all required fields")
    
    if index_name not in index_names or index_needs_update:
        logger.info(f"Creating index: {index_name}")
        index_client.create_index(
            SearchIndex(
                name=index_name,
                fields=[
                    SearchableField(name="chunk_id", key=True, analyzer_name="keyword", sortable=True),
                    SimpleField(name="parent_id", type=SearchFieldDataType.String, filterable=True),
                    SearchableField(name="title"),
                    SearchableField(name="chunk"),
                    SearchField(
                        name="text_vector", 
                        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                        vector_search_dimensions=EMBEDDINGS_DIMENSIONS,
                        vector_search_profile_name="vp",
                        stored=True,
                        hidden=False)
                ],
                vector_search=VectorSearch(
                    algorithms=[
                        HnswAlgorithmConfiguration(name="algo", parameters=HnswParameters(metric=VectorSearchAlgorithmMetric.COSINE))
                    ],
                    vectorizers=[
                        AzureOpenAIVectorizer(
                            name="openai_vectorizer",
                            azure_open_ai_parameters=AzureOpenAIParameters(
                                resource_uri=azure_openai_embedding_endpoint,
                                deployment_id=azure_openai_embedding_deployment,
                                model_name=azure_openai_embedding_model
                            )
                        )
                    ],
                    profiles=[
                        VectorSearchProfile(name="vp", algorithm_configuration_name="algo", vectorizer="openai_vectorizer")
                    ]
                ),
                semantic_search=SemanticSearch(
                    configurations=[
                        SemanticConfiguration(
                            name="default",
                            prioritized_fields=SemanticPrioritizedFields(title_field=SemanticField(field_name="title"), content_fields=[SemanticField(field_name="chunk")])
                        )
                    ],
                    default_configuration_name="default"
                )
            )
        )

    skillsets = indexer_client.get_skillsets()
    skillset_exists = index_name in [skillset.name for skillset in skillsets]
    
    # If index was recreated, we need to recreate the skillset too
    if skillset_exists and index_needs_update:
        logger.info(f"Deleting existing skillset {index_name} to recreate with updated index schema...")
        try:
            indexer_client.delete_skillset(index_name)
            logger.info(f"Deleted skillset {index_name}")
            skillset_exists = False
        except Exception as e:
            logger.error(f"Failed to delete skillset {index_name}: {e}")
            raise
    
    if not skillset_exists:
        logger.info(f"Creating skillset: {index_name}")
        indexer_client.create_skillset(
            skillset=SearchIndexerSkillset(
                name=index_name,
                skills=[
                    SplitSkill(
                        text_split_mode="pages",
                        context="/document",
                        maximum_page_length=2000,
                        page_overlap_length=500,
                        inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                        outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")]),
                    AzureOpenAIEmbeddingSkill(
                        context="/document/pages/*",
                        resource_uri=azure_openai_embedding_endpoint,
                        api_key=None,
                        deployment_id=azure_openai_embedding_deployment,
                        model_name=azure_openai_embedding_model,
                        dimensions=azure_openai_embeddings_dimensions,
                        inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
                        outputs=[OutputFieldMappingEntry(name="embedding", target_name="text_vector")])
                ],
                index_projections=SearchIndexerIndexProjections(
                    selectors=[
                        SearchIndexerIndexProjectionSelector(
                            target_index_name=index_name,
                            parent_key_field_name="parent_id",
                            source_context="/document/pages/*",
                            mappings=[
                                InputFieldMappingEntry(name="chunk", source="/document/pages/*"),
                                InputFieldMappingEntry(name="text_vector", source="/document/pages/*/text_vector"),
                                InputFieldMappingEntry(name="title", source="/document/metadata_storage_name")
                            ]
                        )
                    ],
                    parameters=SearchIndexerIndexProjectionsParameters(
                        projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
                    )
                )))

    # If index was recreated, we need to delete and recreate indexers too
    indexers = indexer_client.get_indexers()
    indexer_exists = False
    if index_needs_update:
        # Delete any existing indexers that reference this index
        for indexer in indexers:
            if indexer.target_index_name == index_name:
                logger.info(f"Deleting indexer {indexer.name} because index was recreated...")
                try:
                    indexer_client.delete_indexer(indexer.name)
                    logger.info(f"Deleted indexer {indexer.name}")
                except Exception as e:
                    logger.warning(f"Failed to delete indexer {indexer.name}: {e}")
    existing_indexer = None
    for idx in indexers:
        if idx.name == index_name:
            indexer_exists = True
            existing_indexer = idx
            break
    
    # Get schedule interval from environment (default: 1 hour)
    schedule_interval_minutes = int(os.environ.get("AZURE_SEARCH_INDEXER_SCHEDULE_MINUTES", "60"))
    schedule_interval_iso8601 = minutes_to_iso8601_duration(schedule_interval_minutes)
    
    if indexer_exists:
        logger.info(f"Indexer {index_name} already exists")
        # Get full indexer details and update with schedule if it doesn't have one
        try:
            full_indexer = indexer_client.get_indexer(index_name)
            if full_indexer.schedule is None:
                logger.info(f"Adding schedule to existing indexer (runs every {schedule_interval_minutes} minutes)")
                full_indexer.schedule = IndexingSchedule(interval=schedule_interval_iso8601)
                indexer_client.create_or_update_indexer(full_indexer)
            else:
                logger.info(f"Indexer already has a schedule: {full_indexer.schedule.interval}")
        except Exception as e:
            logger.warning(f"Could not update indexer schedule: {e}")
    else:
        logger.info(f"Creating indexer: {index_name} with schedule (runs every {schedule_interval_minutes} minutes)")
        indexer_client.create_indexer(
            indexer=SearchIndexer(
                name=index_name,
                data_source_name=index_name,
                skillset_name=index_name,
                target_index_name=index_name,        
                field_mappings=[FieldMapping(source_field_name="metadata_storage_name", target_field_name="title")],
                schedule=IndexingSchedule(interval=schedule_interval_iso8601)
            )
        )

def upload_documents(azure_credential, indexer_name, azure_search_endpoint, azure_storage_endpoint, azure_storage_container):
    indexer_client = SearchIndexerClient(azure_search_endpoint, azure_credential)
    # Upload the documents in /data folder to the blob storage container
    blob_client = BlobServiceClient(
        account_url=azure_storage_endpoint, credential=azure_credential,
        max_single_put_size=4 * 1024 * 1024
    )
    container_client = blob_client.get_container_client(azure_storage_container)
    if not container_client.exists():
        container_client.create_container()
    existing_blobs = [blob.name for blob in container_client.list_blobs()]

    # Open each file in /data folder
    for file in os.scandir("data"):
        with open(file.path, "rb") as opened_file:
            filename = os.path.basename(file.path)
            # Check if blob already exists
            if filename in existing_blobs:
                logger.info("Blob already exists, skipping file: %s", filename)
            else:
                logger.info("Uploading blob for file: %s", filename)
                blob_client = container_client.upload_blob(filename, opened_file, overwrite=True)

    # Start the indexer
    try:
        indexer_client.run_indexer(indexer_name)
        logger.info("Indexer started. Any unindexed blobs should be indexed in a few minutes, check the Azure Portal for status.")
    except ResourceExistsError:
        logger.info("Indexer already running, not starting again")

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s", datefmt="[%X]", handlers=[RichHandler(rich_tracebacks=True)])
    logger = logging.getLogger("voicerag")
    logger.setLevel(logging.INFO)

    logger = logging.getLogger("voicerag")

    load_azd_env()

    logger.info("Checking if we need to set up Azure AI Search index...")
    if os.environ.get("AZURE_SEARCH_REUSE_EXISTING") == "true":
        logger.info("Since an existing Azure AI Search index is being used, no changes will be made to the index.")
        exit()
    else:
        logger.info("Setting up Azure AI Search index and integrated vectorization...")

    # Used to name index, indexer, data source and skillset
    AZURE_SEARCH_INDEX = os.environ["AZURE_SEARCH_INDEX"]
    AZURE_OPENAI_EMBEDDING_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]
    AZURE_OPENAI_EMBEDDING_MODEL = os.environ["AZURE_OPENAI_EMBEDDING_MODEL"]
    EMBEDDINGS_DIMENSIONS = 3072
    AZURE_SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
    AZURE_STORAGE_ENDPOINT = os.environ["AZURE_STORAGE_ENDPOINT"]
    AZURE_STORAGE_CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    AZURE_STORAGE_CONTAINER = os.environ["AZURE_STORAGE_CONTAINER"]

    azure_credential = AzureDeveloperCliCredential(tenant_id=os.environ["AZURE_TENANT_ID"], process_timeout=60)

    setup_index(azure_credential,
        index_name=AZURE_SEARCH_INDEX, 
        azure_search_endpoint=AZURE_SEARCH_ENDPOINT,
        azure_storage_connection_string=AZURE_STORAGE_CONNECTION_STRING,
        azure_storage_container=AZURE_STORAGE_CONTAINER,
        azure_openai_embedding_endpoint=AZURE_OPENAI_EMBEDDING_ENDPOINT,
        azure_openai_embedding_deployment=AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        azure_openai_embedding_model=AZURE_OPENAI_EMBEDDING_MODEL,
        azure_openai_embeddings_dimensions=EMBEDDINGS_DIMENSIONS)

    upload_documents(azure_credential,
        indexer_name=AZURE_SEARCH_INDEX,
        azure_search_endpoint=AZURE_SEARCH_ENDPOINT,
        azure_storage_endpoint=AZURE_STORAGE_ENDPOINT,
        azure_storage_container=AZURE_STORAGE_CONTAINER)