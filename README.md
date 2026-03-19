# Polymarket Trading Bot

Bot automatizado de trading para [Polymarket](https://polymarket.com) con modo de paper trading (simulación) incluido.

## Características

- **Paper Trading**: Simula operaciones con dinero virtual contra datos reales del mercado
- **Análisis de Mercado**: Escanea mercados activos buscando oportunidades
- **Estrategias Múltiples**:
  - Detección de mispricing (arbitraje YES/NO)
  - Mean reversion (reversión a la media)
  - Momentum (seguimiento de tendencia)
- **Gestión de Riesgo**: Kelly criterion, stop-loss, límites de exposición
- **Dashboard CLI**: Monitoreo en tiempo real con tablas formateadas

## Instalación

```bash
# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales (opcional para paper trading)
```

## Uso

```bash
# Escanear mercados (no requiere API key)
python main.py scan

# Ejecutar bot en paper trading (modo prueba)
python main.py run

# Ejecutar N ciclos y parar
python main.py run --cycles 10

# Ver estado del portfolio
python main.py status

# Resetear estado de paper trading
python main.py reset
```

## Configuración

Edita `.env` o `config/settings.py`:

| Variable | Default | Descripción |
|---|---|---|
| `PAPER_TRADING` | `true` | Modo simulación (sin dinero real) |
| `INITIAL_BALANCE` | `1000` | Balance inicial simulado |
| `MAX_POSITION_SIZE` | `50` | Máximo por posición (USD) |
| `MAX_TOTAL_EXPOSURE` | `500` | Exposición total máxima (USD) |

## Arquitectura

```
src/
├── api/
│   ├── polymarket_client.py  # Cliente REST API (CLOB + Gamma)
│   └── paper_trader.py       # Simulador de trading virtual
├── analysis/
│   └── market_analyzer.py    # Escáner y generador de señales
├── risk/
│   └── risk_manager.py       # Gestión de riesgo (Kelly, stop-loss)
├── utils/
│   └── logger.py             # Configuración de logging
├── bot.py                    # Orquestador principal
└── dashboard.py              # Dashboard CLI (rich)
```

## Disclaimer

Este bot es para fines educativos y de investigación. El trading de predicciones implica riesgo financiero. Usa el modo paper trading para probar antes de operar con dinero real.
