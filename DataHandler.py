"""
Data Handler Module

This module contains all functions related to data input/output (I/O)
for the document set processor. It handles:
- Fetching data from the SPARQL endpoint.
- Loading and parsing document set files.
- Saving processed data to JSON.
- Formatting data for human readability.
- Updating the main docset index.
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import unquote_plus

import pandas as pd
import numpy as np
from SPARQLWrapper import SPARQLWrapper, JSON
from TopicDictionary import TopicDictionary

logger = logging.getLogger(__name__)

# --- Public Functions (Called by DocumentSetProcessor) ---

def fetch_docset_metadata(docset_iri: str, sparql_endpoint: str) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Orchestrates the fetching of all metadata from SPARQL.
    Returns a tuple: (DataFrame of papers, Docset Name found in SPARQL or None)
    """
    sparql = SPARQLWrapper(sparql_endpoint)
    sparql.setQuery(_get_docset_full_metadata_query(docset_iri))
    sparql.setReturnFormat(JSON)

    try:
        results = sparql.query().convert()
    except Exception as e:
        logger.error(f"SPARQL query failed: {e}", exc_info=True)
        return pd.DataFrame(), None

    # Extract docset name if available (it's bound to ?docsetName in the query)
    docset_name = None
    bindings = results["results"]["bindings"]
    if bindings:
        first_row = bindings[0]
        if "docsetName" in first_row:
            docset_name = first_row["docsetName"]["value"]

    df_core = pd.DataFrame([{k: v["value"] for k, v in row.items()} for row in bindings])

    if df_core.empty:
        logger.warning("No results returned from SPARQL query.")
        return pd.DataFrame(), None

    # Remove docsetName column from the dataframe as it's not paper metadata
    if "docsetName" in df_core.columns:
        df_core = df_core.drop(columns=["docsetName"])

    logger.info(f"Loaded {len(df_core)} rows (pre-collapse)")
    df_collapsed = _collapse_metadata(df_core)
    logger.info(f"Collapsed to {len(df_collapsed)} unique papers")

    # Fetch multi-valued properties
    properties_to_fetch = {
        "Author": "http://int.fraunhofer.de/linkeddata/ontology/publications#hasAuthor",
        "Organization": "http://int.fraunhofer.de/linkeddata/ontology/publications#contributedAtOrganization",
        "Journal": "http://int.fraunhofer.de/linkeddata/ontology/publications#publishedWithin",
    }

    for prop_name, prop_uri in properties_to_fetch.items():
        logger.info(f"Fetching {prop_name}...")
        df_prop = _fetch_and_group_property(docset_iri, prop_uri, prop_name, sparql_endpoint)
        if not df_prop.empty:
            df_collapsed = pd.merge(df_collapsed, df_prop, on="paper", how="left")

    logger.info(f"Final merged data has {len(df_collapsed)} papers.")
    return df_collapsed, docset_name


def load_docset_from_file(file_path: str) -> pd.DataFrame:
    """
    Loads a docset from a JSON file and prepares the DataFrame.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            docset_json = json.load(f)

        if not docset_json:
            logger.warning("Loaded file is empty.")
            return pd.DataFrame()

        df = pd.DataFrame(docset_json)

        if 'embedding' in df.columns:
            df['embedding'] = df['embedding'].apply(
                lambda x: np.array(x) if isinstance(x, list) else (x if isinstance(x, np.ndarray) else None)
            )
        return df

    except FileNotFoundError:
        logger.warning(f"File not found: {file_path}. A new one will be created if you save.")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Error loading file {file_path}: {e}", exc_info=True)
        return pd.DataFrame()


def load_topics_from_file(file_path: str) -> TopicDictionary:
    """
    Loads a topic dictionary from a JSON file.
    """
    topic_dict = TopicDictionary()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            topic_list: List[Dict[str, Any]] = json.load(f)

        if not topic_list or not isinstance(topic_list, list):
            logger.warning(f"File {file_path} is empty or not a valid topic list.")
            return topic_dict

        max_id = -1
        for topic_data in topic_list:
            if 'id' not in topic_data:
                logger.warning(f"Skipping topic with missing 'id': {topic_data}")
                continue
            topic_id = int(topic_data['id'])
            topic_dict.topics[topic_id] = topic_data
            if topic_id > max_id:
                max_id = topic_id
        topic_dict.topic_counter = max_id + 1
        logger.info(f"Successfully loaded {len(topic_dict.topics)} topics from {file_path}.")

    except FileNotFoundError:
        logger.warning(f"Topic file not found: {file_path}. A new one will be created if you save.")
    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON from {file_path}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred while loading topics: {e}", exc_info=True)

    return topic_dict


def save_docset_to_json(
        df: pd.DataFrame,
        output_path: str,
        human_readable: bool = True
):
    """
    Saves the processed DataFrame to the specified JSON file.
    """
    if df.empty:
        logger.info("No data to save.")
        return

    df_to_save = _create_human_readable_df(df) if human_readable else df.copy()

    if 'embedding' in df_to_save.columns:
        df_to_save['embedding'] = df_to_save['embedding'].apply(
            lambda x: x.tolist() if isinstance(x, np.ndarray) else x
        )

    df_to_save = df_to_save.astype(object).where(pd.notnull(df_to_save), None)
    records = df_to_save.to_dict('records')

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
        logger.info(f"Successfully saved {len(records)} records to {output_path}")
    except Exception as e:
        logger.error(f"Error saving to JSON: {e}", exc_info=True)


def update_docset_index(data_root: str):
    """
    Scans the `data_root` directory and creates/updates the `index.json`.
    The index is now a list of objects: [{"name": "Human Name", "hash": "HashValue", "iri": "..."}]
    """
    index_path = os.path.join(data_root, "index.json")
    docset_list = []
    
    try:
        for item_name in os.listdir(data_root):
            item_path = os.path.join(data_root, item_name)
            if os.path.isdir(item_path):
                # Check for metadata.json
                metadata_path = os.path.join(item_path, "metadata.json")
                
                # Check for data files (using folder name as prefix, which is now the hash)
                docset_file = os.path.join(item_path, f"{item_name}_docset.json")
                topics_file = os.path.join(item_path, f"{item_name}_topics.json")
                
                if os.path.exists(docset_file) and os.path.exists(topics_file):
                    entry = {"hash": item_name}
                    
                    if os.path.exists(metadata_path):
                        try:
                            with open(metadata_path, "r", encoding="utf-8") as f:
                                metadata = json.load(f)
                                entry["name"] = metadata.get("name", item_name)
                                entry["iri"] = metadata.get("iri", "")
                        except Exception as e:
                            logger.warning(f"Failed to read metadata for {item_name}: {e}")
                            entry["name"] = item_name
                    else:
                        # Fallback for legacy folders or missing metadata
                        entry["name"] = item_name
                    
                    docset_list.append(entry)

        # Sort by name
        docset_list.sort(key=lambda x: x["name"])
        
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(docset_list, f, indent=2)
        logger.info(f"Successfully rebuilt docset index. Found {len(docset_list)} valid docsets.")
    except Exception as e:
        logger.error(f"Error updating docset index: {e}", exc_info=True)


# --- Internal SPARQL and Formatting Helpers ---
def _get_docset_full_metadata_query(docset_iri: str) -> str:
    """Returns the main SPARQL query for all paper metadata."""
    query = f"""
        PREFIX fhg: <http://int.fraunhofer.de/linkeddata/ontology/publications#>
        PREFIX dct: <http://purl.org/dc/terms/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX dc: <http://purl.org/dc/elements/1.1/>
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

        SELECT DISTINCT ?paper ?docsetName
                        ?title ?abstract ?doi ?fromYear ?issn ?eissn ?isbn ?eisbn
                        ?language ?status ?publisher ?volume ?issue
                        ?pageBegin ?pageEnd ?pageCount
                        ?hasTimesCited ?referenceCount ?authorCount ?keywordCount ?keywordPlusCount
                        ?uid ?pmid ?pmcid ?open_access ?open_access_url ?url ?parentDOI
                        ?hasTopicWidth ?hasCitedTopicWidth ?hasRefTopicWidth ?hasHIndex
                        ?hasFundingText ?hasFOR ?hasMeshHeading
                        ?hasManualTag ?hasAutoTag ?grantsCount ?addressCount
                        ?meetingAbs ?clinical_trials ?artNo ?bookSeriesTitle ?bookTitle
                        ?redirect_to ?PublicationRef
        WHERE {{
          # 1. Anchor the query to papers within the specified docset
          <{docset_iri}> dct:hasPart ?paper .
          
          # Optional: Get the name of the docset itself
          OPTIONAL {{ <{docset_iri}> rdfs:label ?docsetName . }}

          # 2. Fetch all available optional metadata for each paper
          OPTIONAL {{ ?paper rdfs:label ?title . }}
          OPTIONAL {{ ?paper fhg:abstract ?abstract . }}
          OPTIONAL {{ ?paper fhg:hasDOI ?doi . }}
          OPTIONAL {{ ?paper fhg:fromYear ?fromYear . }}
          OPTIONAL {{ ?paper fhg:issn ?issn . }}
          OPTIONAL {{ ?paper fhg:eissn ?eissn . }}
          OPTIONAL {{ ?paper fhg:isbn ?isbn . }}
          OPTIONAL {{ ?paper fhg:eisbn ?eisbn . }}
          OPTIONAL {{ ?paper fhg:language ?language . }}
          OPTIONAL {{ ?paper fhg:status ?status . }}
          OPTIONAL {{ ?paper fhg:Publisher ?publisher . }}
          OPTIONAL {{ ?paper fhg:inVolume ?volume . }}
          OPTIONAL {{ ?paper fhg:Issue ?issue . }}
          OPTIONAL {{ ?paper fhg:pageBegin ?pageBegin . }}
          OPTIONAL {{ ?paper fhg:pageEnd ?pageEnd . }}
          OPTIONAL {{ ?paper fhg:pageCount ?pageCount . }}
          OPTIONAL {{ ?paper fhg:hasTimesCited ?hasTimesCited . }}
          OPTIONAL {{ ?paper fhg:referenceCount ?referenceCount . }}
          OPTIONAL {{ ?paper fhg:authorCount ?authorCount . }}
          OPTIONAL {{ ?paper fhg:keywordCount ?keywordCount . }}
          OPTIONAL {{ ?paper fhg:keywordPlusCount ?keywordPlusCount . }}
          OPTIONAL {{ ?paper fhg:uid ?uid . }}
          OPTIONAL {{ ?paper fhg:pmid ?pmid . }}
          OPTIONAL {{ ?paper fhg:pmcid ?pmcid . }}
          OPTIONAL {{ ?paper fhg:open_access ?open_access . }}
          OPTIONAL {{ ?paper fhg:open_access_url ?open_access_url . }}
          OPTIONAL {{ ?paper fhg:url ?url . }}
          OPTIONAL {{ ?paper fhg:parentDOI ?parentDOI . }}
          OPTIONAL {{ ?paper fhg:hasTopicWidth ?hasTopicWidth . }}
          OPTIONAL {{ ?paper fhg:hasCitedTopicWidth ?hasCitedTopicWidth . }}
          OPTIONAL {{ ?paper fhg:hasRefTopicWidth ?hasRefTopicWidth . }}
          OPTIONAL {{ ?paper fhg:hasHIndex ?hasHIndex . }}
          OPTIONAL {{ ?paper fhg:hasFundingText ?hasFundingText . }}
          OPTIONAL {{ ?paper fhg:hasFOR ?hasFOR . }}
          OPTIONAL {{ ?paper fhg:hasMeshHeading ?hasMeshHeading . }}
          OPTIONAL {{ ?paper fhg:hasManualTag ?hasManualTag . }}
          OPTIONAL {{ ?paper fhg:hasAutoTag ?hasAutoTag . }}
          OPTIONAL {{ ?paper fhg:grantsCount ?grantsCount . }}
          OPTIONAL {{ ?paper fhg:addressCount ?addressCount . }}
          OPTIONAL {{ ?paper fhg:meetingAbs ?meetingAbs . }}
          OPTIONAL {{ ?paper fhg:clinical_trials ?clinical_trials . }}
          OPTIONAL {{ ?paper fhg:artNo ?artNo . }}
          OPTIONAL {{ ?paper fhg:bookSeriesTitle ?bookSeriesTitle . }}
          OPTIONAL {{ ?paper fhg:Book ?bookTitle . }}
          OPTIONAL {{ ?paper fhg:redirect_to ?redirect_to . }}

          # 3. Correctly fetch only the internal citation links
          OPTIONAL {{
            ?paper fhg:refersTo ?PublicationRef .
            FILTER EXISTS {{ <{docset_iri}> dct:hasPart ?PublicationRef . }}
          }}
        }}
    """
    return query


def _get_multi_value_property_query(docset_iri: str, property_uri: str, var_name: str) -> str:
    """Returns a SPARQL query for a specific multi-valued property."""
    return f"""
        PREFIX dct: <http://purl.org/dc/terms/>
        SELECT ?paper ?{var_name}
        WHERE {{
          <{docset_iri}> dct:hasPart ?paper .
          ?paper <{property_uri}> ?{var_name} .
        }}
    """


def _fetch_and_group_property(
        docset_iri: str,
        property_uri: str,
        var_name: str,
        sparql_endpoint: str
) -> pd.DataFrame:
    """Fetches and groups a multi-valued property (e.g., authors)."""
    sparql = SPARQLWrapper(sparql_endpoint)
    sparql.setQuery(_get_multi_value_property_query(docset_iri, property_uri, var_name))
    sparql.setReturnFormat(JSON)

    try:
        results = sparql.query().convert()
    except Exception as e:
        logger.error(f"SPARQL query failed for property {var_name}: {e}", exc_info=True)
        return pd.DataFrame(columns=["paper", var_name])

    df = pd.DataFrame([{k: v["value"] for k, v in row.items()} for row in results["results"]["bindings"]])
    if df.empty:
        return pd.DataFrame(columns=["paper", var_name])
    return df.groupby("paper")[var_name].apply(list).reset_index()


def _collapse_metadata(df: pd.DataFrame) -> pd.DataFrame:
    """Collapses the metadata DataFrame to one row per paper."""
    candidate_multi_fields = [
        "Author", "Organization", "Journal",
        "hasManualTag", "hasAutoTag", "hasMeshHeading",
        "PublicationRef", "artNo", "bookSeriesTitle", "bookTitle",
        "redirect_to"
    ]

    # Only keep fields that exist in the DataFrame
    multi_fields = [f for f in candidate_multi_fields if f in df.columns]

    # Single-valued fields are all other columns
    single_fields = [c for c in df.columns if c not in multi_fields + ["paper"]]

    # Prepare aggregation dictionary
    agg_dict = {f: (lambda x: list(pd.unique(x.dropna()))) for f in multi_fields}
    agg_dict.update({f: "first" for f in single_fields})

    # Group by paper
    df_collapsed = df.groupby("paper").agg(agg_dict).reset_index()
    return df_collapsed


def _format_iri(iri_value: Optional[str]) -> Optional[str]:
    """Converts a single Fraunhofer IRI into a human-readable string."""
    if not isinstance(iri_value, str) or not iri_value.startswith("http"):
        return iri_value
    try:
        raw_name = iri_value.split('/')[-1]
        decoded_name = unquote_plus(raw_name)
        return decoded_name.split('_', 1)[0] if "/person/" in iri_value else decoded_name.replace('_', ' ')
    except Exception:
        return iri_value


def _apply_format_to_cell(cell_value: Any) -> Any:
    """Applies the _format_iri function to a cell."""
    if isinstance(cell_value, list):
        return [_format_iri(item) for item in cell_value]
    return _format_iri(cell_value) if isinstance(cell_value, str) else cell_value


def _create_human_readable_df(df: pd.DataFrame) -> pd.DataFrame:
    """Creates a copy of the DataFrame with IRI fields formatted."""
    df_hr = df.copy()
    iri_columns = [
        'paper', 'Author', 'Organization', 'Journal',
        'hasManualTag', 'hasAutoTag', 'hasMeshHeading',
        'PublicationRef', 'redirect_to', 'url', 'open_access_url',
        'publisher', 'language', 'status'
    ]

    for col in iri_columns:
        if col in df_hr.columns:
            df_hr[col] = df_hr[col].apply(_apply_format_to_cell)
    return df_hr


if __name__ == "__main__":
    update_docset_index('data')