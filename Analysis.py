"""
Analysis Module

This module contains all core data processing and analysis functions
for the document set processor. It includes:
- SPECTER2 embedding generation.
- Citation network filtering.
- Clustering and dimensionality reduction orchestration.
"""

import logging
from collections import defaultdict
import pandas as pd
import numpy as np
from typing import Optional, Tuple
import torch
import umap

from TopicModeling import run_clustering as run_topic_modeling_clustering
from TopicDictionary import TopicDictionary

logger = logging.getLogger(__name__)


# --- Public Functions (Called by DocumentSetProcessor) ---

def compute_embeddings(
        df: pd.DataFrame,
        model,
        tokenizer,
        device,
        max_length: int = 512
) -> pd.DataFrame:
    """
    Computes and adds CLS embeddings for all papers in the DataFrame.
    """
    logger.info(f"Computing embeddings for {len(df)} papers...")
    sep_token = tokenizer.sep_token or " "
    df_out = df.copy()

    embeddings = [
        _compute_cls_embedding(
            _combine_fields(paper_row, sep_token),
            model, tokenizer, device, max_length=max_length
        )
        for _, paper_row in df.iterrows()
    ]

    df_out['embedding'] = embeddings
    logger.info("Finished computing embeddings.")
    return df_out


def filter_connected_component(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filters the DataFrame to include only papers that are part of
    the internal citation network (connected component).
    """
    logger.info("Filtering to connected component...")
    if 'PublicationRef' not in df.columns:
        logger.warning("No 'PublicationRef' column found. Skipping filter.")
        return df

    all_cited_papers = set(ref for refs in df['PublicationRef'].dropna() if isinstance(refs, list) for ref in refs)
    citing_papers = set(df[df['PublicationRef'].apply(lambda x: isinstance(x, list) and len(x) > 0)]['paper'])
    connected_papers_iris = all_cited_papers.union(citing_papers)

    original_count = len(df)
    df_connected = df[df['paper'].isin(connected_papers_iris)].copy()
    filtered_count = original_count - len(df_connected)

    logger.info(f"Found {len(df_connected)} connected papers. Removing {filtered_count} isolated papers.")
    return df_connected


def run_clustering(
        df: pd.DataFrame,
        topics_output_path: str,
        min_cluster_size: int = 5
) -> Tuple[pd.DataFrame, TopicDictionary]:
    """
    Runs clustering on papers that have embeddings
    and maps the results back to the main DataFrame.
    """
    if 'embedding' not in df.columns:
        logger.warning("No 'embedding' column found. Skipping clustering.")
        return df, TopicDictionary()

    df_with_embeddings = df.dropna(subset=['embedding']).copy()
    if df_with_embeddings.empty:
        logger.warning("No papers with valid embeddings found. Skipping clustering.")
        return df, TopicDictionary()

    logger.info(f"Found {len(df_with_embeddings)} papers with embeddings for clustering.")
    df_with_embeddings = df_with_embeddings.reset_index(drop=True)
    X_embeddings = np.stack(df_with_embeddings['embedding'].values)

    logger.info("Running hierarchical clustering...")
    df_clustered, topic_dict = run_topic_modeling_clustering(
        df_with_embeddings,
        X_embeddings,
        output_path=topics_output_path,
        min_cluster_size=min_cluster_size,
        enable_topic_modeling=True
    )
    logger.info(f"Topic dictionary generated with {len(topic_dict.topics)} topics.")

    logger.info("Mapping cluster data back to main document set...")
    # Columns to be updated or added
    update_cols = ['cluster', 'cluster_name', 'topic_id', 'membership_prob']

    # Drop these columns from the original dataframe if they exist to prevent merge conflicts
    df_pre_merge = df.drop(columns=[col for col in update_cols if col in df.columns], errors='ignore')

    # Select the key ('paper') and the new data from the clustered result
    update_subset = df_clustered[['paper'] + [col for col in update_cols if col in df_clustered.columns]]

    # Merge the new data back in using a left join to preserve all original rows
    df_out = pd.merge(df_pre_merge, update_subset, on='paper', how='left')

    return df_out, topic_dict


def run_dimensionality_reduction(
        df: pd.DataFrame,
        umap_neighbors: int,
        umap_min_dist: float
        ) -> pd.DataFrame:
    """Runs UMAP dimensionality reduction on the embeddings."""
    if 'embedding' not in df.columns:
        logger.warning("No 'embedding' column found. Skipping dimensionality reduction.")
        return df

    df_with_embeddings = df.dropna(subset=['embedding']).copy()
    if df_with_embeddings.empty:
        logger.warning("No papers with valid embeddings found. Skipping dimensionality reduction.")
        return df

    embeddings = np.stack(df_with_embeddings['embedding'].values)
    logger.info(f"Reducing {len(embeddings)} embeddings to 2D using UMAP...")

    if umap_neighbors >= len(embeddings):
        logger.warning(f"n_neighbors ({umap_neighbors}) >= n_samples ({len(embeddings)}). Adjusting to {len(embeddings) - 1}.")
        umap_neighbors = len(embeddings) - 1

    df_2d_source = df_with_embeddings.copy()
    if umap_neighbors <= 0:
        logger.warning("Not enough samples for UMAP. Filling 2D coordinates with 0.")
        df_2d_source['x_2d'], df_2d_source['y_2d'] = 0.0, 0.0
    else:
        reducer = umap.UMAP(
            n_components=2, metric='cosine', random_state=42,
            n_neighbors=umap_neighbors, min_dist=umap_min_dist
        )
        embeddings_2d = reducer.fit_transform(embeddings)
        df_2d_source['x_2d'] = embeddings_2d[:, 0]
        df_2d_source['y_2d'] = embeddings_2d[:, 1]

    logger.info("Mapping 2D data back to main document set...")
    # Columns to be updated or added
    update_cols = ['x_2d', 'y_2d']

    # Drop these columns from the original dataframe if they exist to prevent merge conflicts
    df_pre_merge = df.drop(columns=[col for col in update_cols if col in df.columns], errors='ignore')

    # Select the key ('paper') and the new data from the 2D result
    update_subset = df_2d_source[['paper'] + [col for col in update_cols if col in df_2d_source.columns]]

    # Merge the new data back in using a left join to preserve all original rows
    df_out = pd.merge(df_pre_merge, update_subset, on='paper', how='left')


    logger.info("Dimensionality reduction complete.")
    return df_out


def calculate_and_add_citation_flow(docset_df: pd.DataFrame, topic_dict: TopicDictionary) -> TopicDictionary:
    """
    Calculates yearly citation statistics for each cluster and adds it to the topic dictionary.

    For each cluster, this function calculates:
    1.  `paper_count`: Number of papers published in a given year.
    2.  `total_citations_on_papers`: Sum of 'hasTimesCited' for papers published in that year.
    3.  `internal_citations_received`: Total citations received in that year from other papers within the dataset.
    4.  `citations_from_other_clusters`: A breakdown of citations received from other specific clusters and years.
    """
    # --- Data Cleaning ---
    clean_df = docset_df.copy()
    clean_df['fromYear'] = pd.to_numeric(clean_df['fromYear'], errors='coerce')
    clean_df['hasTimesCited'] = pd.to_numeric(clean_df['hasTimesCited'], errors='coerce').fillna(0).astype(int)
    clean_df.dropna(subset=['fromYear', 'cluster'], inplace=True)
    clean_df['fromYear'] = clean_df['fromYear'].astype(int)

    # Map paper IRI to cluster ID
    paper_to_cluster = clean_df.set_index('paper')['cluster'].to_dict()

    # Initialize citation flow with nested structure for citing clusters and years
    citation_flow = defaultdict(
        lambda: defaultdict(
            lambda: {
                'total': 0,
                'from': defaultdict(lambda: defaultdict(int))
            }
        )
    )
    # Process each paper's citations
    paper_to_year = clean_df.set_index('paper')['fromYear'].to_dict()
    for _, citing_paper_row in clean_df.iterrows():
        citing_year = int(citing_paper_row['fromYear'])
        citing_cluster = citing_paper_row['cluster']

        if 'PublicationRef' not in citing_paper_row or not isinstance(citing_paper_row['PublicationRef'], list):
            continue

        for cited_paper_iri in citing_paper_row['PublicationRef']:
            cited_cluster = paper_to_cluster.get(cited_paper_iri)
            cited_year = paper_to_year.get(cited_paper_iri)

            if cited_cluster is None or cited_year is None:
                continue

            record = citation_flow[cited_cluster][cited_year]
            record['total'] += 1
            record['from'][citing_cluster][citing_year] += 1

    # Aggregate publication stats per cluster/year
    yearly_publication_stats = clean_df.groupby(['cluster', 'fromYear']).agg(
        paper_count=('paper', 'size'),
        total_citations_on_papers=('hasTimesCited', 'sum')
    ).reset_index()

    # Add stats to topic dictionary
    for topic_id, topic_data in topic_dict.topics.items():
        cluster_label = topic_data['cluster_label']
        yearly_stats_list = []

        publication_years = yearly_publication_stats[yearly_publication_stats['cluster'] == cluster_label]
        citation_years = set(citation_flow.get(cluster_label, {}).keys())
        all_years = sorted(list(set(publication_years['fromYear'].astype(int)) | citation_years))

        for year in all_years:
            stats_for_year = {'year': year}

            # Publication stats
            pub_stats = publication_years[publication_years['fromYear'] == year]
            if not pub_stats.empty:
                stats_for_year['paper_count'] = int(pub_stats.iloc[0]['paper_count'])
                stats_for_year['total_citations_on_papers'] = int(pub_stats.iloc[0]['total_citations_on_papers'])
            else:
                stats_for_year['paper_count'] = 0
                stats_for_year['total_citations_on_papers'] = 0

            # Citation stats
            citations = citation_flow.get(cluster_label, {}).get(year)
            if citations:
                stats_for_year['internal_citations_received'] = citations['total']
                stats_for_year['citations_from_other_clusters'] = {
                    citing_cluster: dict(years_dict)
                    for citing_cluster, years_dict in citations['from'].items()
                }
            else:
                stats_for_year['internal_citations_received'] = 0
                stats_for_year['citations_from_other_clusters'] = {}

            yearly_stats_list.append(stats_for_year)

        topic_dict.topics[topic_id]['yearly_stats'] = yearly_stats_list

    return topic_dict


# --- Internal Helper Functions ---

def _normalize_text(s: Optional[str]) -> str:
    """Helper to clean and normalize text fields."""
    return str(s).strip() if s else ""


def _combine_fields(paper: pd.Series, sep_token: str) -> str:
    """Combines title and abstract with a SEP token for SPECTER2."""
    title = _normalize_text(paper.get("title"))
    abstract = _normalize_text(paper.get("abstract"))
    return f"{title}{sep_token}{abstract}"


def _compute_cls_embedding(
        text: str,
        model,
        tokenizer,
        device,
        max_length: int = 512
) -> Optional[np.ndarray]:
    """Tokenizes text and returns the CLS-pooled embedding."""
    if not text.strip(tokenizer.sep_token or " "):
        return None
    try:
        inputs = tokenizer(
            text, padding='max_length', truncation=True,
            max_length=max_length, return_tensors='pt'
        ).to(device)
        with torch.no_grad():
            outputs = model(**inputs)
            cls_emb = outputs.last_hidden_state[:, 0, :].cpu().numpy()[0]
        norm = np.linalg.norm(cls_emb)
        return cls_emb / norm if norm > 0 else cls_emb
    except Exception as e:
        logger.error(f"Error during embedding computation: {e}", exc_info=True)
        return None
