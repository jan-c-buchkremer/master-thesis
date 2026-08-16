import json
import os
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
import logging

import numpy as np
import pandas as pd
import hdbscan
import umap
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.metrics import silhouette_score
from nltk.tokenize import RegexpTokenizer
from nltk.stem import WordNetLemmatizer
from dotenv import load_dotenv
from openai import OpenAI
from TopicDictionary import TopicDictionary

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Suppress third-party logs
for lib in ["groq", "httpx", "httpcore", "httpcore.http11", "skimage"]:
    logging.getLogger(lib).setLevel(logging.ERROR)

load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


def _df_to_docset(df: pd.DataFrame, local_cluster_col: str = None) -> List[Dict]:
    docset = []
    for _, row in df.iterrows():
        doc = {
            "title": row["title"],
            "abstract": row.get("abstract", ""),
            "cluster": row[local_cluster_col] if local_cluster_col else row.get("cluster")
        }
        doc["text"] = f"{doc['title']} {doc['abstract']}"
        docset.append(doc)
    return docset


class LDATopicExtractor:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("OpenRouter API key is required.")
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        self.lemmatizer = WordNetLemmatizer()
        self.tokenizer = RegexpTokenizer(r'\w+')

    def preprocess_text(self, text: str) -> str:
        text = text.lower().replace('[^\w\s]', ' ')
        tokens = self.tokenizer.tokenize(text)
        return ' '.join([
            self.lemmatizer.lemmatize(token)
            for token in tokens
            if len(token) >= 3 and not token.isdigit()
        ])

    def extract_global_lda_topics(self, texts: List[str], n_topics: int = 5,
                                  n_top_words: int = 10) -> List[List[str]]:
        if len(texts) < 3:
            return [["insufficient_data"]]
        vectorizer = CountVectorizer(stop_words='english', max_df=0.85, min_df=2)
        try:
            X = vectorizer.fit_transform(texts)
            lda = LatentDirichletAllocation(n_components=n_topics, random_state=42)
            lda.fit(X)
            feature_names = vectorizer.get_feature_names_out()
            topics = []
            for topic in lda.components_:
                top_terms = [feature_names[i] for i in topic.argsort()[:-n_top_words - 1:-1]]
                topics.append(top_terms)
            return topics
        except Exception as e:
            logger.warning(f"Global LDA extraction failed: {e}")
            return [["extraction_failed"]]

    def extract_distinctive_words(self, cluster_texts_map: Dict[int, List[str]],
                                  n_top_words: int = 10) -> Dict[int, List[str]]:
        results = {}
        if not cluster_texts_map:
            return results
        if len(cluster_texts_map) == 1:
            label, texts = next(iter(cluster_texts_map.items()))
            try:
                vec = CountVectorizer(stop_words='english', max_features=n_top_words)
                vec.fit(texts)
                results[label] = list(vec.get_feature_names_out())
            except Exception:
                results[label] = ["extraction_failed"]
            return results

        labels, cluster_docs = zip(*cluster_texts_map.items())
        joined_docs = [" ".join(texts) for texts in cluster_docs]
        try:
            vectorizer = TfidfVectorizer(stop_words='english', max_df=0.9, min_df=1)
            tfidf_matrix = vectorizer.fit_transform(joined_docs)
            feature_names = vectorizer.get_feature_names_out()
            for i, label in enumerate(labels):
                scores = tfidf_matrix[i].toarray().ravel()
                top_indices = scores.argsort()[-n_top_words:][::-1]
                top_words = [feature_names[idx] for idx in top_indices if scores[idx] > 0]
                results[label] = top_words if top_words else ["general_topic"]
        except Exception as e:
            logger.error(f"c-TF-IDF extraction failed: {e}")
            for label in cluster_texts_map:
                results[label] = ["extraction_failed"]
        return results

    def generate_topic_description(self, topic_lists: List[List[str]],
                                   parent_description: str = "",
                                   sentences: int = 2,
                                   representative_titles: Optional[List[str]] = None) -> str:
        topic_texts = ["; ".join(words) for words in topic_lists]
        topics_combined = " | ".join(topic_texts)
        titles_clause = ""
        if representative_titles:
            titles_joined = " | ".join(representative_titles)
            titles_clause = f" A few representative paper titles from this cluster are: '{titles_joined}'."
        prompt = (
            f"Generate a concise description in {sentences} sentence(s) for a research cluster. "
            f"This cluster is a sub-topic of a larger area described as: '{parent_description}'. "
            f"The cluster's unique, distinctive keywords are: '{topics_combined}'.{titles_clause} "
            f"Combine the parent context with these unique keywords to create a specific description for this sub-cluster. "
            f"Focus on the new, specific information. Write in academic style."
        )
        try:
            response = self.client.chat.completions.create(
                model='mistralai/mistral-small-3.2-24b-instruct',
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"LLM description generation failed: {e}")
            return "Description unavailable."

    def generate_cluster_name(self, parent_description: str, topic_description: str,
                              sibling_names: Optional[List[str]] = None) -> str:
        if not topic_description:
            return "Unnamed Cluster"
        avoid_clause = ""
        if sibling_names:
            avoid_clause = (
                f" Avoid reusing or closely paraphrasing these existing sibling topic names: "
                f"{', '.join(sibling_names)}. Pick a name that clearly distinguishes this cluster from them."
            )
        prompt = (
            f"You are tasked with naming a research cluster. "
            f"Its parent topic is: '{parent_description}'. "
            f"Here is this clusters description: {topic_description} "
            f"Generate a fitting title for this subtopic. For this find the unifying concept within its description. "
            f"Find a name of maximum 5 words that reflects all topic words at once.{avoid_clause} "
            f"Only return the name (max 5 words), nothing else."
        )
        try:
            response = self.client.chat.completions.create(
                model='mistralai/mistral-small-3.2-24b-instruct',
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=30
            )
            name = _clean_llm_name(response.choices[0].message.content)
            return name if name else "Unnamed Cluster"
        except Exception as e:
            logger.error(f"LLM naming failed: {e}")
            return "Unnamed Cluster"

    def extract_internal_words(self, cluster_texts: List[str], n_top_words: int = 10) -> List[str]:
        if not cluster_texts:
            return []
        try:
            vec = CountVectorizer(stop_words='english', max_df=0.8, min_df=2, max_features=n_top_words)
            vec.fit(cluster_texts)
            return list(vec.get_feature_names_out())
        except Exception:
            return ["extraction_failed"]


class ClusterQualityMetrics:
    @staticmethod
    def compute_silhouette(embeddings: np.ndarray, labels: np.ndarray) -> float:
        unique_labels = np.unique(labels)
        if len(unique_labels) < 2 or len(labels) < 2:
            return -1.0
        try:
            return silhouette_score(embeddings, labels, metric='euclidean')
        except Exception:
            return -1.0



def _merge_small_clusters(local_labels: np.ndarray, reduced_embeddings: np.ndarray, min_size: int) -> np.ndarray:
    """
    Reassigns every point in a group smaller than `min_size` to the nearest (by centroid
    distance) group that meets the floor, so undersized HDBSCAN groups never get promoted
    to their own topic. Pure relabeling; returns a new label array of the same shape.

    If no group meets the floor (e.g. everything is small), the original labels are
    returned unchanged - the caller's existing "can't split further" fallback then applies.
    """
    labels = local_labels.copy()
    unique, counts = np.unique(labels, return_counts=True)
    survivors = unique[counts >= min_size]

    if len(survivors) == 0:
        # Nothing clears the floor - collapse everything into the single largest group so
        # the caller's "can't split further" fallback kicks in, instead of leaving a pile
        # of tiny groups that would each get promoted to their own topic.
        largest = unique[np.argmax(counts)]
        labels[:] = largest
        return labels

    if len(survivors) == len(unique):
        return labels

    centroids = {lbl: reduced_embeddings[labels == lbl].mean(axis=0) for lbl in unique}
    survivor_centroids = np.array([centroids[lbl] for lbl in survivors])

    for lbl in unique:
        if lbl in survivors:
            continue
        dists = np.linalg.norm(survivor_centroids - centroids[lbl], axis=1)
        target = survivors[np.argmin(dists)]
        labels[labels == lbl] = target

    return labels


def _clean_llm_name(raw: str, max_words: int = 5) -> Optional[str]:
    """
    Strips markdown/quote noise from an LLM-generated cluster name and rejects anything
    that still looks like conversational text (a rejected suggestion, a preamble sentence)
    rather than a short title, so callers can fall back to a safe default instead of
    writing garbage into the topic tree.
    """
    if not raw:
        return None
    name = raw.strip().splitlines()[0].strip()
    name = re.sub(r'^\d+[.)]\s*', '', name)
    name = name.replace('*', '').replace('"', '').replace("'", '').strip()
    if not name or len(name.split()) > max_words + 3 or len(name) > 60:
        return None
    return name


def _normalize_topic_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _token_set_jaccard(a: str, b: str) -> float:
    tokens_a, tokens_b = set(_normalize_topic_name(a).split()), set(_normalize_topic_name(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def deduplicate_topic_names(topic_dict: "TopicDictionary", extractor: "LDATopicExtractor",
                            jaccard_threshold: float = 0.8) -> None:
    """
    Finds exact and near-duplicate topic names anywhere in the tree (not just siblings) and
    asks the LLM to pick a distinct replacement name for each member of a colliding group,
    using each topic's own description plus its ancestor breadcrumb as disambiguating context.
    """
    non_root_ids = [tid for tid, t in topic_dict.topics.items() if t.get("parent") is not None]

    groups: List[List[int]] = []
    seen = set()
    for tid in non_root_ids:
        if tid in seen:
            continue
        name = topic_dict.topics[tid]["name"]
        group = [tid]
        for other_id in non_root_ids:
            if other_id == tid or other_id in seen:
                continue
            other_name = topic_dict.topics[other_id]["name"]
            if _normalize_topic_name(name) == _normalize_topic_name(other_name) or \
                    _token_set_jaccard(name, other_name) >= jaccard_threshold:
                group.append(other_id)
        if len(group) > 1:
            groups.append(group)
            seen.update(group)

    for group in groups:
        breadcrumbs = {}
        for tid in group:
            ancestors = topic_dict.get_ancestor_chain(tid)
            breadcrumbs[tid] = " > ".join(a["name"] for a in reversed(ancestors))

        members_desc = "\n".join(
            f"{i + 1}. Current name: '{topic_dict.topics[tid]['name']}' | Path: {breadcrumbs[tid]} | "
            f"Description: {topic_dict.topics[tid]['description']}"
            for i, tid in enumerate(group)
        )
        prompt = (
            f"The following {len(group)} research clusters ended up with the same or a near-identical name, "
            f"even though their descriptions differ. Give each one a distinct, specific name (max 5 words) "
            f"that reflects what makes it different from the others, based on its own description and its "
            f"position in the topic tree.\n{members_desc}\n"
            f"Respond with exactly {len(group)} lines, one new name per line, in the same order, nothing else."
        )
        try:
            response = extractor.client.chat.completions.create(
                model='mistralai/mistral-small-3.2-24b-instruct',
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=100,
            )
            raw_lines = [line for line in response.choices[0].message.content.strip().splitlines() if line.strip()]
            if len(raw_lines) != len(group):
                logger.warning(f"Dedup rename count mismatch for group {group}: got {len(raw_lines)} lines")
            else:
                for tid, raw_line in zip(group, raw_lines):
                    cleaned = _clean_llm_name(raw_line)
                    if cleaned:
                        topic_dict.topics[tid]["name"] = cleaned
                    else:
                        logger.warning(f"Dedup rename rejected malformed name for topic {tid}: {raw_line!r}")
        except Exception as e:
            logger.error(f"Dedup rename failed for group {group}: {e}")


def run_clustering(
        df_with_embeddings: pd.DataFrame,
        embeddings: np.ndarray,
        output_path: str,
        min_cluster_size: int = 15,
        enable_topic_modeling: bool = True,
        max_leaf_size: int = 150,
        min_topic_size: int = 15,
) -> Tuple[pd.DataFrame, TopicDictionary]:
    topic_dict = TopicDictionary()
    quality_metrics = ClusterQualityMetrics()
    topic_extractor = LDATopicExtractor(api_key=OPENROUTER_API_KEY) if enable_topic_modeling else None

    def _generate_cluster_info(df, indices, labels, reduced_embeddings, extractor, depth, parent_description):
        cluster_texts_map = defaultdict(list)
        preprocessed_texts_map = defaultdict(list)
        titles_map = defaultdict(list)
        embeddings_map = defaultdict(list)
        for idx, local_label, emb in zip(indices, labels, reduced_embeddings):
            row = df.iloc[idx]
            text = f"{row['title']} {row.get('abstract', '')}"
            cluster_texts_map[local_label].append(text)
            preprocessed_texts_map[local_label].append(extractor.preprocess_text(text))
            titles_map[local_label].append(row['title'])
            embeddings_map[local_label].append(emb)

        distinctive_words_map = extractor.extract_distinctive_words(preprocessed_texts_map, n_top_words=7)
        internal_words_map = {label: extractor.extract_internal_words(texts, n_top_words=7)
                              for label, texts in preprocessed_texts_map.items()}

        def _representative_titles(label, n=3):
            titles = titles_map[label]
            if len(titles) <= n:
                return titles
            embs = np.array(embeddings_map[label])
            centroid = embs.mean(axis=0)
            order = np.argsort(np.linalg.norm(embs - centroid, axis=1))[:n]
            return [titles[i] for i in order]

        results = {}
        all_labels = set(distinctive_words_map.keys()) | set(internal_words_map.keys())
        # largest clusters first, so bigger/clearer topics claim the more obvious names first
        ordered_labels = sorted(all_labels, key=lambda l: -len(cluster_texts_map[l]))
        assigned_names: List[str] = []
        for label in ordered_labels:
            combined_words = list(dict.fromkeys(internal_words_map.get(label, []) +
                                                distinctive_words_map.get(label, [])))
            valid_words = [w for w in combined_words if w not in ["too_few_documents", "extraction_failed"]]

            if not valid_words:
                results[label] = {"desc": "Too few documents.", "name": f"Cluster {label}", "words": []}
                continue

            desc = extractor.generate_topic_description([valid_words], parent_description, 2,
                                                         representative_titles=_representative_titles(label))
            name = extractor.generate_cluster_name(parent_description, desc, sibling_names=assigned_names)
            assigned_names.append(name)
            results[label] = {"desc": desc, "name": name, "words": valid_words}
        return results

    def _recursive_cluster_step(
            all_embeddings: np.ndarray,
            indices_to_cluster: List[int],
            df_clustered: pd.DataFrame,
            label_prefix: str,
            original_df: pd.DataFrame,
            parent_topic_id: Optional[int],
            parent_description: str,
            depth: int = 0,
            is_retry: bool = False
    ) -> None:
        n_current = len(indices_to_cluster)
        current_label = label_prefix.rstrip('_') if label_prefix else "0"

        if n_current < min_cluster_size:
            _finalize_cluster(indices_to_cluster, df_clustered, current_label, parent_topic_id, parent_description)
            return

        # UMAP Logic
        n_neighbors = max(2, min(int(n_current * (0.02 if is_retry else 0.05)), n_current - 1))

        # k in UMAP spectral init is ~ n_components + 1, must be < n_current
        n_components = min(20, n_current - 2)

        if n_components < 2:
            _finalize_cluster(indices_to_cluster, df_clustered, current_label, parent_topic_id, parent_description)
            return

        umap_model = umap.UMAP(
            n_neighbors=n_neighbors,
            min_dist=0.0,
            n_components=n_components,
            metric='cosine',
            random_state=42,
        )
        reduced_embeddings = umap_model.fit_transform(all_embeddings[indices_to_cluster])

        # HDBSCAN Logic
        hdbscan_model = hdbscan.HDBSCAN(
            min_cluster_size=max(2, min(min_cluster_size, n_current - 1)),
            min_samples=2 if is_retry else None,
            metric='euclidean',
            cluster_selection_method='leaf' if is_retry else 'eom',
            cluster_selection_epsilon=0.1 if is_retry else 0.3,
            prediction_data=True
        )
        hdbscan_model.fit(reduced_embeddings)
        local_labels = hdbscan_model.labels_

        # Safer Noise Reassignment
        try:
            all_membership_vectors = hdbscan.all_points_membership_vectors(hdbscan_model)
            if all_membership_vectors is not None and len(all_membership_vectors.shape) >= 2:
                if np.any(local_labels == -1) and all_membership_vectors.shape[1] > 0:
                    noise_indices = np.where(local_labels == -1)[0]
                    local_labels[noise_indices] = np.argmax(all_membership_vectors[noise_indices], axis=1)
        except Exception as e:
            logger.warning(f"Noise reassignment skipped: {e}")

        # Merge undersized groups (incl. anything this aggressive retry carved out as a
        # near-singleton) into their nearest sufficiently-large sibling before they can be
        # promoted to their own topic.
        local_labels = _merge_small_clusters(local_labels, reduced_embeddings, min_topic_size)

        unique_labels = np.unique(local_labels)

        # Forced Splitting Logic
        if len(unique_labels) <= 1 and not is_retry:
            return _recursive_cluster_step(all_embeddings, indices_to_cluster, df_clustered, label_prefix,
                                           original_df, parent_topic_id,
                                           parent_description, depth, is_retry=True)

        if len(unique_labels) <= 1 and is_retry:
            _finalize_cluster(indices_to_cluster, df_clustered, current_label, parent_topic_id, parent_description)
            return

        if not is_retry:
            silhouette = quality_metrics.compute_silhouette(reduced_embeddings, local_labels)
            logger.info(f"Cluster {current_label}: split into {len(unique_labels)} groups, silhouette={silhouette:.3f}")

        cluster_info_map = _generate_cluster_info(original_df, indices_to_cluster, local_labels, reduced_embeddings,
                                                  topic_extractor, depth, parent_description)

        index_map = dict(zip(range(n_current), indices_to_cluster))
        for local_label in unique_labels:
            new_label = f"{label_prefix}{local_label}"
            cluster_indices = [index_map[i] for i in np.where(local_labels == local_label)[0]]
            info = cluster_info_map.get(local_label, {"desc": "...", "name": f"Cluster {new_label}"})

            topic_id = topic_dict.add_topic(
                cluster_label=new_label, name=info["name"], description=info["desc"],
                parent_id=parent_topic_id, papers=original_df.iloc[cluster_indices].index.tolist()
            )

            if len(cluster_indices) <= max_leaf_size:
                _finalize_cluster(cluster_indices, df_clustered, new_label, topic_id, info["name"])
            else:
                _recursive_cluster_step(all_embeddings, cluster_indices, df_clustered, f"{new_label}_",
                                        original_df, topic_id, info["desc"], depth + 1)

    def _finalize_cluster(indices, df, label, topic_id, name):
        df.loc[indices, 'cluster'] = label
        df.loc[indices, 'cluster_name'] = name
        df.loc[indices, 'topic_id'] = topic_id if topic_id is not None else -1

    # Global Execution
    all_texts = [topic_extractor.preprocess_text(f"{r['title']} {r.get('abstract', '')}")
                 for _, r in df_with_embeddings.iterrows()]
    global_topics = topic_extractor.extract_global_lda_topics(all_texts, n_topics=5)
    global_description = topic_extractor.generate_topic_description(global_topics, "Entire collection", 3)

    root_topic_id = topic_dict.add_topic("root", "All Documents", global_description, None,
                                         df_with_embeddings.index.tolist())

    df_clustered = df_with_embeddings.reset_index(drop=True).copy()
    _recursive_cluster_step(embeddings, df_clustered.index.tolist(), df_clustered, "",
                            df_with_embeddings, root_topic_id, global_description, 0)

    # Resolve any exact/near-duplicate names anywhere in the tree before saving
    if enable_topic_modeling and len(topic_dict.topics) > 1:
        deduplicate_topic_names(topic_dict, topic_extractor)

    # Assign colors to leaf nodes before saving
    if enable_topic_modeling and len(topic_dict.topics) > 1:
        topic_dict.assign_leaf_colors()

    topic_dict.save_to_json(output_path)
    return df_clustered, topic_dict