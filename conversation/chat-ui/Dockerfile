# Agent Chat UI (LangChain, MIT) en una versión fija, configurada para Aegra.
FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/* \
 && corepack enable
WORKDIR /app
RUN git clone https://github.com/langchain-ai/agent-chat-ui.git . && git checkout 0bc78bf
RUN pnpm install --frozen-lockfile
# Dos ajustes verificados (el build falla si el código de esa versión cambia):
# - historial completo: con el límite por defecto (10 puntos) y una corrida larga, como subir
#   tres documentos, la pantalla calculaba mal la punta del hilo y el siguiente mensaje salía
#   desde un punto anterior (una rama sin los documentos);
# - `$` como texto: con dos montos en una línea («$2,643.55 … $2,643.72») remark-math los
#   dibujaba como fórmula.
RUN sed -i 's/fetchStateHistory: true,/fetchStateHistory: { limit: 1000 },/' src/providers/Stream.tsx \
 && sed -i 's/remarkPlugins={\[remarkGfm, remarkMath\]}/remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}/' src/components/thread/markdown-text.tsx \
 && grep -q "fetchStateHistory: { limit: 1000 }" src/providers/Stream.tsx \
 && grep -q "singleDollarTextMath: false" src/components/thread/markdown-text.tsx
# Sin NEXT_PUBLIC_API_URL ni NEXT_PUBLIC_ASSISTANT_ID a propósito: con ellos la UI oculta el
# formulario inicial, que es donde se escribe el token del cliente (campo «API Key»).
COPY customer-view.mjs document-progress.tsx customer-view.test.mjs ./src/components/thread/
COPY patch-customer-view.mjs ./
RUN node patch-customer-view.mjs && node --test src/components/thread/customer-view.test.mjs
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build
EXPOSE 3000
CMD ["pnpm", "start", "-p", "3000"]
