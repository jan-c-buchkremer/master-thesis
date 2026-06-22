/**
 * Calculates a normalized cosine distance (1 - cosine_similarity).
 * Assumes vectors are pre-normalized (unit vectors).
 * Returns 1.0 (max distance) if vectors are invalid.
 *
 * @param {Array<number>} vecA - The first vector.
 * @param {Array<number>} vecB - The second vector.
 * @returns {number} The cosine distance, from 0.0 (identical) to 1.0 (dissimilar).
 */
export function cosineDistanceNormalized(vecA, vecB) {
    if (!vecA || !vecB || vecA.length !== vecB.length) {
        return 1.0; // Max distance for invalid inputs
    }

    let dotProduct = 0;
    for (let i = 0; i < vecA.length; i++) {
        dotProduct += vecA[i] * vecB[i];
    }

    // Assuming vectors are normalized, dotProduct is cosine similarity.
    // Distance = 1.0 - similarity
    // Clamping to [0, 1] range to avoid floating point precision issues (e.g., 1.0000001)
    return Math.max(0, Math.min(1.0, 1.0 - dotProduct));
}

/**
 * Formats a list for display in tooltips, truncating it if it exceeds a max length.
 *
 * @param {Array<string>} list - The array of items to format.
 * @param {number} max - The maximum number of items to show.
 * @returns {string} A formatted, comma-separated string.
 */
export const formatList = (list, max) => {
    if (!list || list.length === 0) return 'N/A';

    const truncated = list.slice(0, max).join(', ');

    return list.length > max ? `${truncated}...` : truncated;
};