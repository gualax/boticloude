# Polymarket Trading Bot

Bot de trading automatizado para [Polymarket](https://polymarket.com) con modo
de simulacion (paper trading), valoracion de mercados de Bitcoin a partir de
datos reales, y un agente que aprende de sus propias operaciones.

---

## Que hace

| Modulo | Que aporta |
|---|---|
| **Estrategias de mercado** | Detecta mispricing YES/NO, reversion a la media y momentum |
| **Modelo Bitcoin** | Valora mercados de umbral BTC/ETH/SOL con precio spot y volatilidad reales |
| **Hermes** | Agente que aprende de cada trade cerrado y veta o redimensiona los siguientes |
| **Gestion de riesgo** | Kelly fraccional, stop-loss, take-profit, trailing stop, cooldowns |
| **Reporte diario IA** | Analiza el dia y auto-ajusta parametros dentro de limites de seguridad |
| **Panel web** | Dashboard en vivo con login, graficos y todo lo que Hermes ha aprendido |

---

## Instalacion

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # y edita .env
```

Para simular no necesitas ninguna clave. Para el panel con contrasena y para
Hermes si hacen falta un par de valores en `.env` (mas abajo).

---

## Uso

```bash
python main.py web          # Panel + bot en http://localhost:8080
python main.py run          # Sin interfaz, corre indefinidamente
python main.py scan         # Oportunidades detectadas ahora mismo
python main.py crypto       # Precios BTC/ETH/SOL + comprobacion del modelo
python main.py hermes       # Que ha aprendido Hermes hasta ahora
python main.py status       # Estado del portfolio
python main.py reset        # Borra el estado simulado
```

`reset` conserva la memoria de Hermes. Para borrarla tambien: `--include-hermes`.

---

## El modelo de Bitcoin

Polymarket lista mercados del tipo *"Will Bitcoin reach $150,000 by June?"*. El
bot toma el precio spot real y la volatilidad realizada (30 dias, de Binance con
CoinGecko como respaldo) y calcula la probabilidad justa. La diferencia contra
el precio del mercado es una ventaja que las estrategias de historial de precios
no pueden ver.

Se manejan las dos formas de pregunta:

- **"on / at" (terminal)** — probabilidad de terminar por encima del umbral:
  `P(S_T > K) = N(d2)`
- **"by / before" (barrera)** — probabilidad de tocarlo en algun momento, con la
  formula exacta de primer paso para movimiento browniano con deriva

Comprueba el modelo con datos en vivo:

```bash
python main.py crypto
```

---

## Hermes — el agente que aprende

Hermes se situa entre la generacion de senales y la ejecucion. Hace tres cosas,
y mejora en las tres cuanto mas tiempo lleva el bot corriendo.

**1. Recuerda.** Guarda el contexto completo de cada posicion: que estrategia la
abrio, con cuanto edge y confianza, y como termino.

**2. Reflexiona.** Cada N trades cerrados (10 por defecto) envia esos resultados
a Gemini y le pide que los destile en reglas condicionales. Por ejemplo:

> `momentum` + `price_below: 0.15` → **avoid** (confianza 0.8, 7 trades)

**3. Decide.** Aplica esas reglas a cada senal nueva antes de que se mueva
dinero. Puede vetar el trade o cambiar su tamano. Las reglas son datos
estructurados, no texto, asi que decidir no cuesta ninguna llamada al modelo.

Ademas de las reglas aprendidas, hay un limite duro: si una estrategia baja del
30% de aciertos con al menos 15 trades de muestra, Hermes la apaga.

Lo que Hermes aprende sobrevive a los reinicios (`data/hermes/memory.json`).

### Barreras de seguridad

- Una leccion necesita al menos 3 trades de evidencia para ser aceptada
- Solo se admiten condiciones y acciones de una lista cerrada
- La confianza se recorta al rango 0–1
- El multiplicador de tamano nunca sale de 0.25x–1.5x
- Las lecciones por debajo de 0.4 de confianza no se aplican

---

## Auto-ajuste diario

A la hora configurada (23:00 por defecto) el bot manda el dia entero a Gemini y
recibe un informe con recomendaciones. Los cambios de parametros se aplican
solos, pero **siempre** dentro de los limites de `TUNABLE_PARAMS` en
`config/settings.py`. Si Gemini pide algo fuera de rango, se recorta al limite.

Puedes lanzarlo a mano desde el panel con **Ejecutar Ahora**.

---

## Acceso al panel

Sin `DASHBOARD_PASSWORD` el panel queda abierto, lo cual solo es razonable en
localhost. Para protegerlo:

```bash
# en .env
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=una_contrasena_larga
SECRET_KEY=<pega aqui la salida del comando de abajo>
```

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Con `SECRET_KEY` fija, la sesion sobrevive a los reinicios del bot. Hay bloqueo
temporal de 5 minutos tras 5 intentos fallidos desde la misma IP.

---

## Configuracion

Todo vive en `config/settings.py` y puede sobrescribirse desde `.env`.

### Riesgo

| Parametro | Def. | Que controla |
|---|---|---|
| `MAX_POSITION_SIZE` | 50 | Maximo por posicion (USD) |
| `MAX_TOTAL_EXPOSURE` | 500 | Exposicion total maxima (USD) |
| `MAX_EXPOSURE_PER_MARKET` | 60 | Tope por pregunta de mercado |
| `KELLY_FRACTION` | 0.25 | Fraccion del criterio de Kelly |
| `STOP_LOSS_PCT` | 0.15 | Stop-loss por posicion |
| `TAKE_PROFIT_PCT` | 0.40 | Cierre automatico en ganancia |
| `TRAILING_STOP_ACTIVATION` | 0.15 | Ganancia que activa el trailing stop |
| `TRAILING_STOP_DISTANCE` | 0.08 | Caida desde el pico que lo dispara |
| `LOSS_COOLDOWN_CYCLES` | 50 | Ciclos de veda tras perder en un mercado |
| `MIN_PRICE` / `MAX_PRICE` | 0.05 / 0.95 | Evita tokens de centimos y casi resueltos |

### Hermes

| Parametro | Def. | Que controla |
|---|---|---|
| `HERMES_ENABLED` | true | Activa el agente |
| `HERMES_MODEL` | gemini-2.5-pro | Modelo que usa como cerebro |
| `HERMES_REFLECT_EVERY_N_TRADES` | 10 | Cada cuantos trades reflexiona |
| `HERMES_MIN_SAMPLE` | 15 | Muestra minima para vetar una estrategia |
| `HERMES_VETO_WIN_RATE` | 0.30 | Tasa de aciertos por debajo de la cual la apaga |
| `HERMES_MIN_EVIDENCE` | 3 | Trades minimos para aceptar una leccion |

---

## Arquitectura

```
src/
├── agents/
│   ├── hermes.py            Agente: recuerda, reflexiona, decide
│   └── hermes_memory.py     Memoria persistente (lecciones + historial)
├── analysis/
│   ├── market_analyzer.py   Escaner y estrategias de mercado
│   ├── crypto_strategy.py   Modelo lognormal para umbrales de BTC/ETH/SOL
│   └── daily_report.py      Informe diario + auto-ajuste de parametros
├── api/
│   ├── polymarket_client.py CLOB + Gamma
│   ├── crypto_client.py     Spot y volatilidad (Binance / CoinGecko)
│   └── paper_trader.py      Simulador con slippage
├── risk/risk_manager.py     Kelly, stops, cooldowns, exposicion
├── auth.py                  Login del panel
├── bot.py                   Orquestador
└── web.py                   Servidor Flask + API JSON
```

---

## Tests

```bash
python -m pytest tests/ -v
```

91 tests cubren el simulador, el modelo de Bitcoin, el aprendizaje de Hermes,
la autenticacion y la API del panel.

---

## Aviso

Esto es software educativo y de investigacion. El trading en mercados de
prediccion conlleva riesgo real de perdida. Corre en modo simulacion el tiempo
suficiente para entender su comportamiento antes de plantearte dinero real, y
recuerda que los resultados simulados no predicen los reales.
