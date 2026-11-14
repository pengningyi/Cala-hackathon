import { expect, test } from "@playwright/test";
import { linkCitations } from "../lib/chat-citations";

const ids = ["s15", "s16", "s1", "s12", "s65", "s66", "s80", "s83"];
const link = (n: number) => `[${n}](#cite-${n})`;

test("reference groups become adjacent numbered citation links", () => {
  expect(linkCitations("invested ([s15], [s16], [s1], [s12]).", ids)).toBe(`invested ${link(1)}${link(2)}${link(3)}${link(4)}.`);
  expect(linkCitations("Madrid (s65, s66)", ids)).toBe(`Madrid ${link(5)}${link(6)}`);
  expect(linkCitations("founder [s80, s83]", ids)).toBe(`founder ${link(7)}${link(8)}`);
  expect(linkCitations("a proxy [s15]", ids)).toBe(`a proxy ${link(1)}`);
  expect(linkCitations("(see s65, s66)", ids)).toBe(`(see ${link(5)}${link(6)})`);
});

test("non-references, unknown IDs and URLs are left alone", () => {
  expect(linkCitations("Series B; s99 is unknown", ids)).toBe("Series B; s99 is unknown");
  expect(linkCitations("https://example.test/s15 and docs.s15", ids)).toBe("https://example.test/s15 and docs.s15");
  expect(linkCitations("No citations [s15]", [])).toBe("No citations [s15]");
});
