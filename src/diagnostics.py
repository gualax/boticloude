"""Explains, step by step, why the bot is or is not trading.

Runs the real scan and the real filters — not a reimplementation — so the
funnel it prints is exactly what a live cycle does.
"""
import logging
from collections import Counter

logger = logging.getLogger(__name__)

LINE = "─" * 66


def _heading(text: str):
    print(f"\n{text}\n{LINE}")


def explain_connection_error(error: Exception) -> tuple[str, list[str]]:
    """Turn a network failure into a named cause and concrete next steps.

    The generic advice ("check your connection") is useless when the error
    already says precisely what went wrong.
    """
    text = str(error).lower()

    if "hostname mismatch" in text or "certificate verify failed" in text:
        return (
            "TLS interceptado — el certificado no es de Polymarket",
            [
                "Algo en tu red esta descifrando el trafico HTTPS y presentando",
                "su propio certificado. Casi siempre es una de estas tres:",
                "",
                "  - Cortafuegos corporativo con inspeccion SSL",
                "  - Antivirus con escaneo HTTPS (Kaspersky, ESET, Avast...)",
                "  - Un portal cautivo de wifi publico sin aceptar todavia",
                "",
                "Para confirmar quien intercepta, mira quien firma el certificado:",
                "  openssl s_client -connect gamma-api.polymarket.com:443 \\",
                "      -servername gamma-api.polymarket.com </dev/null 2>/dev/null \\",
                "      | openssl x509 -noout -issuer -subject",
                "",
                "Si el emisor es tu empresa o tu antivirus, ahi tienes la causa.",
                "Prueba desde otra red (el movil compartiendo datos sirve).",
                "",
                "NO desactives la verificacion de certificados: dejaria el trafico",
                "expuesto y no arregla el bloqueo de fondo.",
            ],
        )

    if "reset by peer" in text or "connection aborted" in text:
        return (
            "Conexion cortada por el servidor",
            [
                "Polymarket esta cerrando la conexion. Causas habituales:",
                "",
                "  - Tu IP quedo limitada por exceso de peticiones (puede tardar",
                "    horas en soltarse aunque el bot ya se porte bien)",
                "  - Bloqueo por region: Polymarket restringe algunas jurisdicciones",
                "  - Un cortafuegos corta el dominio",
                "",
                "Prueba desde otra red para distinguir entre las tres.",
            ],
        )

    if "timed out" in text or "timeout" in text:
        return (
            "Sin respuesta a tiempo",
            [
                "El servidor no contesto. Puede ser una caida temporal de",
                "Polymarket o una conexion muy lenta. Reintenta en unos minutos.",
            ],
        )

    if "name or service not known" in text or "nodename nor servname" in text:
        return (
            "El dominio no resuelve",
            [
                "El DNS no encuentra el servidor. Revisa tu conexion, o si un",
                "DNS filtrado esta bloqueando el dominio.",
            ],
        )

    if "proxy" in text or "403" in text or "forbidden" in text:
        return (
            "Acceso denegado por un intermediario",
            [
                "Un proxy o el propio Polymarket rechazo la peticion.",
                "Si estas en una red corporativa o con VPN, prueba sin ella.",
            ],
        )

    return ("Fallo de red", ["Revisa tu conexion, VPN o cortafuegos."])


def diagnose(bot, limit: int = 100, show: int = 12) -> dict:
    """Walk one full cycle's decision path and print where signals are lost.

    Always returns the same keys so callers can read the funnel without
    checking which stage it stopped at.
    """
    config = bot.config
    # Every stage stays present even when an earlier one ends the walk
    summary = {
        "error": None,
        "cause": None,
        "markets_raw": 0,
        "markets_suitable": 0,
        "signals": 0,
        "passed_risk": 0,
        "approved_hermes": 0,
        "tradeable": 0,
    }

    _heading("1. Conexion")
    try:
        raw = bot.client.get_markets(limit=limit, active=True)
        summary["markets_raw"] = len(raw)
        print(f"   Mercados devueltos por la API : {len(raw)}")
    except Exception as e:
        cause, advice = explain_connection_error(e)
        print(f"   NO SE PUDO CONECTAR\n")
        print(f"   Causa: {cause}\n")
        for line in advice:
            print(f"   {line}" if line else "")
        print(f"\n   Error original:\n   {e}")
        print("\n   Sin acceso a la API de Polymarket el bot no puede operar,")
        print("   y esto no se arregla desde el codigo del bot.")
        summary["error"] = str(e)
        summary["cause"] = cause
        return summary

    if not raw:
        print("\n   La API respondio pero sin mercados. Nada que analizar.")
        return summary

    _heading("2. Filtro de mercados")
    markets = bot.analyzer.scan_markets(limit=limit)
    summary["markets_suitable"] = len(markets)
    print(f"   Aptos para operar             : {len(markets)} de {len(raw)}")
    if markets:
        liquidity = sorted((m["liquidity"] for m in markets), reverse=True)
        print(f"   Liquidez maxima               : ${liquidity[0]:,.0f}")
        print(f"   Liquidez mediana              : "
              f"${liquidity[len(liquidity) // 2]:,.0f}")
        above = sum(1 for x in liquidity if x >= config.MIN_LIQUIDITY)
        print(f"   Con liquidez >= ${config.MIN_LIQUIDITY:<15,.0f}: {above}")
        if above == 0:
            print(f"\n   Ningun mercado alcanza MIN_LIQUIDITY (${config.MIN_LIQUIDITY:,.0f}).")
            print("   Bajalo en config/settings.py o en .env para operar mas.")
    else:
        print("\n   Ningun mercado paso el filtro basico (liquidez o tokens).")
        return summary

    _heading("3. Senales generadas")
    signals = bot.analyzer.generate_signals(markets)
    summary["signals"] = len(signals)
    print(f"   Senales totales               : {len(signals)}")
    if signals:
        by_strategy = Counter(s.strategy for s in signals)
        for name, count in by_strategy.most_common():
            print(f"     - {name:16s}: {count}")
    else:
        print("\n   Ninguna estrategia encontro oportunidad en este momento.")
        print("   Es un resultado normal: si nada esta mal valorado, no se opera.")
        return summary

    _heading("4. Filtros de riesgo")
    passed, rejected = [], []
    for signal in signals:
        reason = bot.risk.rejection_reason(signal)
        (rejected if reason else passed).append((signal, reason))

    summary["passed_risk"] = len(passed)
    print(f"   Pasan los filtros             : {len(passed)} de {len(signals)}")
    if rejected:
        causes = Counter(r.split(" ")[0] for _, r in rejected)
        print("   Motivos de rechazo:")
        for cause, count in causes.most_common():
            print(f"     - {cause:16s}: {count}")
        print("\n   Ejemplos:")
        for signal, reason in rejected[:show]:
            print(f"     x {signal.market_name[:44]:44s} {reason}")

    if not passed:
        print("\n   Todas las senales fueron filtradas. El bot esta funcionando,")
        print("   pero sus umbrales actuales no dejan pasar nada.")
        _suggest_loosening(config)
        return summary

    _heading("5. Criterio de Hermes")
    approved = []
    for signal, _ in passed:
        verdict = bot.hermes.evaluate_signal(signal)
        if verdict.approved:
            approved.append((signal, verdict))
        else:
            print(f"     x {signal.market_name[:44]:44s} {verdict.summary}")
    summary["approved_hermes"] = len(approved)
    print(f"   Aprobadas por Hermes          : {len(approved)} de {len(passed)}")

    _heading("6. Tamano de posicion")
    would_trade = []
    for signal, verdict in approved:
        size = bot.risk.size_position(signal, bot.paper.balance,
                                      len(bot.paper.positions))
        size = round(size * verdict.size_multiplier, 1)
        cost = size * signal.current_price
        if size > 0 and cost >= 1.0:
            would_trade.append((signal, size, cost))
        else:
            print(f"     x {signal.market_name[:44]:44s} "
                  f"tamano calculado ${cost:.2f} < minimo $1")

    summary["tradeable"] = len(would_trade)
    print(f"   Operables ahora mismo         : {len(would_trade)}")
    if would_trade:
        print("\n   El bot compraria:")
        for signal, size, cost in would_trade[:show]:
            print(f"     > {signal.market_name[:40]:40s} "
                  f"{signal.side:4s} ${signal.current_price:.3f} "
                  f"x{size:>7.1f} = ${cost:>6.2f}  "
                  f"[{signal.strategy}, edge {signal.edge * 100:.1f}%]")

    _heading("Resumen")
    print(f"   {len(raw)} mercados -> {len(markets)} aptos -> {len(signals)} senales")
    print(f"   -> {len(passed)} pasan riesgo -> {len(approved)} aprueba Hermes "
          f"-> {len(would_trade)} operables")

    if not would_trade:
        print("\n   El bot funciona, pero hoy nada supera sus umbrales.")
        _suggest_loosening(config)
    else:
        print(f"\n   Hay {len(would_trade)} operaciones disponibles. Si el bot esta")
        print("   corriendo, las tomara en su proximo ciclo.")

    return summary


def _suggest_loosening(config):
    """Point at the knobs that would let more trades through."""
    print("\n   Para operar mas, afloja en config/settings.py o .env:")
    print(f"     MIN_EDGE       actual {config.MIN_EDGE:<6} (prueba 0.02)")
    print(f"     MIN_LIQUIDITY  actual {config.MIN_LIQUIDITY:<6} (prueba 100)")
    print(f"     MIN_PRICE      actual {config.MIN_PRICE:<6} (prueba 0.04)")
