// Adaptación acotada de la versión upstream fijada en Dockerfile; falla si cambia el contrato.
import { readFileSync, writeFileSync } from "node:fs";

function patch(path, changes) {
  let source = readFileSync(path, "utf8");
  for (const [before, after] of changes) {
    if (source.split(before).length !== 2) throw new Error(`Ancla no única en ${path}: ${before}`);
    source = source.replace(before, after);
  }
  writeFileSync(path, source);
}

patch("src/components/thread/messages/ai.tsx", [
  ['import { getContentString } from "../utils";', 'import { getContentString } from "../utils";\nimport { customerText } from "../customer-view.mjs";'],
  ['const contentString = getContentString(content);', 'const contentString = customerText(getContentString(content), isLoading);'],
  ['parseAsBoolean.withDefault(false)', 'parseAsBoolean.withDefault(true)'],
  ['if (isToolResult && hideToolCalls) {', 'if (hideToolCalls && !isToolResult && !contentString && !(isLastMessage && threadInterrupt) && !thread.values.ui?.some((ui) => ui.metadata?.message_id === message?.id)) return null;\n\n  if (isToolResult && hideToolCalls) {'],
]);
patch("src/components/thread/index.tsx", [
  ['import { HumanMessage } from "./messages/human";', 'import { HumanMessage } from "./messages/human";\nimport { DocumentProgress } from "./document-progress";'],
  ['"hideToolCalls",\n    parseAsBoolean.withDefault(false)', '"hideToolCalls",\n    parseAsBoolean.withDefault(true)'],
  ['{/* Special rendering case where there are no AI/tool messages, but there is an interrupt.', '<DocumentProgress messages={messages} loading={isLoading} />\n                  {/* Special rendering case where there are no AI/tool messages, but there is an interrupt.'],
]);
