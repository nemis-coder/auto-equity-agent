import { Message } from "@langchain/langgraph-sdk";
import { documentProgress } from "./customer-view.mjs";

export function DocumentProgress({ messages, loading }: { messages: Message[]; loading: boolean }) {
  const items = documentProgress(messages, loading);
  if (!items.length) return null;
  return (
    <section role="status" aria-live="polite" aria-label="Progreso de documentos"
      className="rounded-lg border p-3 text-sm">
      <p className="mb-2 font-medium">Revisión de documentos</p>
      <ul className="space-y-1">
        {items.map((item: { id: string; label: string; status: string }) => (
          <li key={item.id}><span className="font-medium">{item.label}:</span> {item.status}</li>
        ))}
      </ul>
    </section>
  );
}
