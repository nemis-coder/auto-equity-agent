# Informe de evaluación: pista de IA real

Estado: **PASS** · muestra `holdout` · 3 repeticiones · 2026-09-25T20:06:30.352536+00:00

- exactitud de extraccion: 497/498 (99.8 %)
- abstencion correcta: 30/30 (100.0 %)
- falsos ok documentales: 0/15 (0.0 %)
- contaminacion de listos: 0/9 (0.0 %)
- completitud de camino valido: 9/9 (100.0 %)

La muestra retenida es pequeña (S6): no demuestra desempeño productivo.

## Notas de la ejecución (2026-09-25)

- **Código:** `0dff95b`; `claude-opus-5` directo, prompt `extractor-v2`, muestra `holdout` (8 expedientes × 3 documentos × 3 repeticiones = 72 lecturas). Tokens totales: 499,512 de entrada y 99,121 de salida.
- **Correcciones previas necesarias:** la primera corrida falló la identificación en 7 de 8 expedientes por dos defectos, ya corregidos en `0dff95b`:
  - el runner abría un event loop por caso, y la primera llamada de cada caso encontraba cerradas las conexiones del cliente HTTP;
  - el límite de 20 s por llamada era corto para una identificación con Opus (ahora 60 s para leer documentos).

  Una corrida de verificación con 1 repetición también dio PASS.
- **Único campo incorrecto (1/498):** la periodicidad del recibo en `ho-05`, en una de las tres repeticiones. El expediente se detuvo igual por su regla esperada (`INCOME_CURRENCY`).
- **`ho-06` sin la regla esperada:** es la variante sin periodicidad. Claude se abstuvo, correctamente, de leerla, y la regla de calidad de extracción detuvo el expediente antes que `INCOME_PERIOD`. El resultado es seguro (no llega a listo), aunque lo marca otra regla.
- El prompt no se ajustó mirando esta muestra: sigue siendo retenida.
