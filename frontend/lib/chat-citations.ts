export const CITE_PREFIX = "#cite-";

/**
 * Turn the answer's reference IDs ("[s15]", "(s65, s66)", "[s80, s83]") into numbered
 * citation links that match the order of the "sources" list under the answer.
 * Unknown IDs are left as written. Parentheses or brackets that held only references are dropped.
 */
export function linkCitations(text: string, citationIds: string[]): string {
  if (!citationIds.length) return text;
  const index = new Map(citationIds.map((id, position) => [id, position + 1]));
  const link = (n: number) => `[${n}](${CITE_PREFIX}${n})`;
  const cite = String.raw`\[\d+\]\(${CITE_PREFIX}\d+\)`;
  // Not preceded by a word character, "/", "." or "-", so URLs and words such as "Series" are untouched.
  let out = text.replace(/(?<![\w/.-])\[?(s\d+)\b\]?/g, (match, id: string) => {
    const n = index.get(id);
    return n === undefined ? match : link(n);
  });
  // "([1](#cite-1), [2](#cite-2))" -> "[1](#cite-1)[2](#cite-2)"
  out = out.replace(new RegExp(String.raw`[(\[]\s*((?:${cite}(?:\s*(?:,|;|and)\s*)?)+)\s*[)\]]`, "g"), (_, group: string) => group);
  out = out.replace(new RegExp(String.raw`(${cite})\s*(?:,|;)?\s*(?=${cite})`, "g"), "$1");
  return out;
}
