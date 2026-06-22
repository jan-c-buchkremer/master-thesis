"""
DocumentSetProcessor

A single class to orchestrate the extraction and annotation of research paper
document sets.

This class coordinates other modules to perform the pipeline:
- data_handler.py: Handles all SPARQL queries and file I/O.
- analysis.py: Executes all data processing (embedding, filtering, clustering).
- topic_dictionary.py: Provides the data structure for topics.
- topic_modeling.py: Contains the core clustering and LLM logic.
"""

import os
import logging
import hashlib
import json
from typing import Dict, List, Optional, Callable
import pandas as pd
import torch
from transformers import AutoTokenizer
from adapters import AutoAdapterModel

# --- Refactored Imports ---
# Import the modules containing the moved logic
import DataHandler
import Analysis
from TopicDictionary import TopicDictionary
# --- End Refactored Imports ---

logger = logging.getLogger(__name__)

class DocumentSetProcessor:
    """
    Orchestrates the complete data extraction and annotation pipeline
    for a research paper document set.
    """

    def __init__(
            self,
            sparql_endpoint: str = os.getenv('SPARQL_ENDPOINT', 'http://ks2:8890/sparql'),
            base_model_dir: str = "models/specter2_base_model",
            adapter_dir: str = "models/specter2_adapter",
            data_root: str = "data",
            umap_neighbors: int = 5,
            umap_min_dist: float = 0.1,
            min_cluster_size: int = 5
    ):
        """
        Initializes the processor, sets up data paths, and loads
        the SPECTER2 model into memory.
        """
        logger.info("Initializing DocumentSetProcessor...")
        self.sparql_endpoint = sparql_endpoint
        self.base_model_dir = base_model_dir
        self.adapter_dir = adapter_dir
        self.data_root = data_root
        os.makedirs(self.data_root, exist_ok=True)

        self.umap_neighbors = umap_neighbors
        self.umap_min_dist = umap_min_dist
        self.min_cluster_size = min_cluster_size

        self.docset_name: Optional[str] = None
        self.docset_hash: Optional[str] = None
        self.docset_iri: Optional[str] = None
        self.docset_dir: Optional[str] = None
        self.docset_output_path: Optional[str] = None
        self.topics_output_path: Optional[str] = None
        self.metadata_path: Optional[str] = None

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"Using device: {self.device}")

        self.tokenizer = None
        self.model = None

        self.docset_df = pd.DataFrame()
        self.topic_dict = TopicDictionary()

        self._load_specter_model()

    def _load_specter_model(self):
        """Loads the tokenizer and adapter model from the specified paths."""
        try:
            logger.info(f"Loading SPECTER2 model from {self.base_model_dir} and adapter from {self.adapter_dir}")
            self.tokenizer = AutoTokenizer.from_pretrained(self.base_model_dir)
            self.model = AutoAdapterModel.from_pretrained(self.base_model_dir)
            self.model.load_adapter(
                self.adapter_dir,
                source="local",
                load_as="proximity",
                set_active=True
            )
            self.model.to(self.device)
            self.model.eval()
            logger.info("SPECTER2 model loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading SPECTER2 model: {e}", exc_info=True)
            self.model = None
            self.tokenizer = None

    @staticmethod
    def hash_iri(iri: str) -> str:
        """Generates an MD5 hash from the IRI."""
        return hashlib.md5(iri.encode('utf-8')).hexdigest()

    def _set_output_paths(self, docset_hash: str):
        """Sets the internal output paths using the docset hash."""
        self.docset_hash = docset_hash
        self.docset_dir = os.path.join(self.data_root, self.docset_hash)
        self.docset_output_path = os.path.join(self.docset_dir, f"{self.docset_hash}_docset.json")
        self.topics_output_path = os.path.join(self.docset_dir, f"{self.docset_hash}_topics.json")
        self.metadata_path = os.path.join(self.docset_dir, "metadata.json")
        
        os.makedirs(self.docset_dir, exist_ok=True)
        logger.info(f"Output directory set to: {self.docset_dir}")

    def _save_metadata(self):
        """Saves the docset metadata (name, IRI, hash) to a JSON file, preserving existing status."""
        if not self.metadata_path:
            return
        
        metadata = {
            "name": self.docset_name,
            "iri": self.docset_iri,
            "hash": self.docset_hash
        }
        
        # Preserve existing status if file exists
        if os.path.exists(self.metadata_path):
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    if "status" in existing_data:
                        metadata["status"] = existing_data["status"]
                    if "error" in existing_data:
                        metadata["error"] = existing_data["error"]
            except Exception as e:
                logger.warning(f"Could not read existing metadata to preserve status: {e}")

        try:
            with open(self.metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"Saved metadata to {self.metadata_path}")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")

    def update_status(self, new_status: str, error_message: Optional[str] = None):
        """Updates the status field in the metadata.json file."""
        if not self.metadata_path or not os.path.exists(self.metadata_path):
            logger.warning("Metadata file not found. Cannot update status.")
            return

        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            
            metadata["status"] = new_status
            if error_message:
                metadata["error"] = error_message
            elif "error" in metadata and new_status != "error":
                 # Clear error if status is not error
                 metadata.pop("error", None)

            with open(self.metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            
            logger.info(f"Updated status to '{new_status}' in {self.metadata_path}")
        except Exception as e:
            logger.error(f"Failed to update status in metadata file: {e}")

    def process_from_iri(
            self,
            docset_iri: str,
            docset_name: Optional[str] = None,
            save_human_readable: bool = True,
            status_callback: Optional[Callable[[str], None]] = None
    ):
        """Runs the full processing pipeline starting from a docset IRI."""
        logger.info(f"Starting new process from IRI: {docset_iri}")
        
        self.docset_iri = docset_iri
        # If docset_name is provided (e.g. from API), use it initially.
        # It might be overwritten if SPARQL returns a name.
        self.docset_name = docset_name 
        docset_hash = self.hash_iri(docset_iri)
        
        self._set_output_paths(docset_hash)
        self._save_metadata() # Save metadata early
        
        self.topic_dict = TopicDictionary()

        if status_callback:
            status_callback("fetching_metadata")

        logger.info("Fetching metadata from SPARQL endpoint...")
        # Updated to receive docset_name from SPARQL
        self.docset_df, fetched_name = DataHandler.fetch_docset_metadata(docset_iri, self.sparql_endpoint)

        if fetched_name:
            logger.info(f"Found docset name in SPARQL: {fetched_name}")
            self.docset_name = fetched_name
            # Update metadata with the new name
            self._save_metadata()

        if self.docset_df.empty:
            logger.warning("No data fetched. Aborting process.")
            return pd.DataFrame()
        logger.info(f"Successfully fetched {len(self.docset_df)} papers.")

        # Run the full pipeline
        self.run_processing_steps(
            start_from='all',
            human_readable=save_human_readable,
            status_callback=status_callback
        )
        return self.get_dataframe()

    def load_and_process_file(
            self,
            docset_name: str, # Kept for backward compatibility, but logic might need adjustment if using hash
            start_from: str = 'all',
            save_human_readable: bool = True,
            status_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Loads a docset from a JSON file. 
        NOTE: This method assumes 'docset_name' is the folder name. 
        If using hashes, pass the hash as 'docset_name'.
        """
        self._set_output_paths(docset_name)
        logger.info(f"Starting new process for Docset (Hash/Name): {docset_name} (start_from='{start_from}')")

        logger.info(f"Loading data from {self.docset_output_path}...")
        self.docset_df = DataHandler.load_docset_from_file(self.docset_output_path)
        self.topic_dict = DataHandler.load_topics_from_file(self.topics_output_path)

        if self.docset_df.empty and start_from != 'all':
            logger.warning("Loaded file is empty. Aborting.")
            return pd.DataFrame()
        logger.info(f"Successfully loaded {len(self.docset_df)} papers.")

        self.run_processing_steps(
            start_from=start_from,
            human_readable=save_human_readable,
            status_callback=status_callback
        )
        return self.get_dataframe()

    def run_processing_steps(
            self,
            start_from: str = 'auto',
            human_readable: bool = True,
            status_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Runs the annotation steps on the currently loaded data.
        `start_from` can be one of: 'embed', 'filter', 'cluster', 'reduce', 'flow', 'all', 'auto'.
        """
        if self.docset_df.empty and start_from not in ['all']:
            logger.error("No data loaded. Please run `process_from_iri` or `load_and_process_file` first.")
            return

        steps = ['embed', 'filter', 'cluster', 'reduce', 'flow']
        steps_to_run = []

        if start_from in steps:
            start_index = steps.index(start_from)
            steps_to_run = steps[start_index:]
        elif start_from == 'all':
            steps_to_run = steps
        elif start_from == 'auto':
            if 'embedding' not in self.docset_df.columns or self.docset_df['embedding'].isnull().all():
                steps_to_run.extend(steps)
            else:
                if 'cluster' not in self.docset_df.columns:
                    steps_to_run.append('cluster')
                if 'x_2d' not in self.docset_df.columns:
                    steps_to_run.append('reduce')
                flow_present = False
                if not self.topic_dict.is_empty():
                    first_topic = next(iter(self.topic_dict.topics.values()), None)
                    if first_topic and 'yearly_stats' in first_topic:
                        flow_present = True
                if not flow_present:
                    steps_to_run.append('flow')
        else:
            logger.error(f"Invalid start_from value: '{start_from}'.")
            return

        logger.info(f"Processing steps to run: {steps_to_run}")

        for step in steps_to_run:
            if step == 'embed':
                if status_callback: status_callback("computing_embeddings")
                self._run_embed_step(human_readable)
            elif step == 'filter':
                if status_callback: status_callback("filtering_network")
                self._run_filter_step(human_readable)
            elif step == 'cluster':
                if status_callback: status_callback("clustering")
                self._run_cluster_step(human_readable)
            elif step == 'reduce':
                if status_callback: status_callback("reducing_dimensions")
                self._run_reduce_step(human_readable)
            elif step == 'flow':
                if status_callback: status_callback("calculating_flow")
                self._run_flow_step(human_readable)

        logger.info("Processing complete.")

    def _run_embed_step(self, human_readable: bool):
        if self.model and self.tokenizer:
            logger.info("--- Running: Embedding ---")
            self.docset_df = Analysis.compute_embeddings(
                self.docset_df, self.model, self.tokenizer, self.device
            )
            self._save_results(human_readable)
        else:
            logger.warning("Model not loaded. Skipping embedding computation.")

    def _run_filter_step(self, human_readable: bool):
        logger.info("--- Running: Filtering ---")
        self.docset_df = Analysis.filter_connected_component(self.docset_df)
        self._save_results(human_readable)

    def _run_cluster_step(self, human_readable: bool):
        logger.info("--- Running: Clustering ---")
        self.docset_df, self.topic_dict = Analysis.run_clustering(
            self.docset_df, self.topics_output_path, self.min_cluster_size
        )
        self._save_results(human_readable)

    def _run_reduce_step(self, human_readable: bool):
        logger.info("--- Running: Dimensionality Reduction ---")
        self.docset_df = Analysis.run_dimensionality_reduction(
            self.docset_df, self.umap_neighbors, self.umap_min_dist
        )
        self._save_results(human_readable)

    def _run_flow_step(self, human_readable: bool):
        logger.info("--- Running: Citation Flow Calculation ---")
        self.topic_dict = Analysis.calculate_and_add_citation_flow(
            self.docset_df, self.topic_dict
        )
        self._save_results(human_readable)

    def _save_results(self, human_readable: bool = True):
        """Saves the final docset DataFrame and the TopicDictionary."""
        if not self.docset_output_path or not self.topics_output_path:
            logger.error("Output paths not set. Cannot save results.")
            return

        if not self.docset_df.empty:
            logger.info(f"Saving docset to {self.docset_output_path}...")
            DataHandler.save_docset_to_json(
                self.docset_df, self.docset_output_path, human_readable=human_readable
            )
        else:
            logger.warning("Docset DataFrame is empty. Skipping save.")

        if not self.topic_dict.is_empty():
            logger.info(f"Saving topics to {self.topics_output_path}...")
            self.topic_dict.save_to_json(self.topics_output_path)
        else:
            logger.warning("Topic dictionary is empty. Skipping save.")

        DataHandler.update_docset_index(self.data_root)

    def get_dataframe(self) -> pd.DataFrame:
        """Returns the current state of the document set as a DataFrame."""
        return self.docset_df

    def get_json(self) -> List[Dict]:
        """Returns the current state as a list of dictionaries."""
        df_temp = self.docset_df.astype(object).where(pd.notnull(self.docset_df), None)
        return df_temp.to_dict('records')

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.info("--- DocumentSetProcessor ---")

    processor = DocumentSetProcessor(
        base_model_dir="models/specter2_base_model",
        adapter_dir="models/specter2_adapter",
        umap_min_dist=0.25,
        min_cluster_size=15
    )

    # Example usage with hash (assuming folder exists)
    docset = processor.load_and_process_file(
         docset_name="Gut Microbiomes",
         start_from='all'
     )
