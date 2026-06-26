import os
import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ["DB_PORT"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
FOCUS_TOKEN = os.environ["FOCUS_TOKEN"]
FOCUS_BASE_URL = os.environ["FOCUS_BASE_URL"]
CNPJ_EMITENTE = os.environ["CNPJ_EMITENTE"]
EMITTER_STATE = os.environ["EMITTER_STATE"]
NOME_EMITENTE = os.environ["NOME_EMITENTE"]
NOME_FANTASIA_EMITENTE = os.environ["NOME_FANTASIA_EMITENTE"]
LOGRADOURO_EMITENTE = os.environ["LOGRADOURO_EMITENTE"]
NUMERO_EMITENTE = os.environ["NUMERO_EMITENTE"]
BAIRRO_EMITENTE = os.environ["BAIRRO_EMITENTE"]
MUNICIPIO_EMITENTE = os.environ["MUNICIPIO_EMITENTE"]
CEP_EMITENTE = os.environ["CEP_EMITENTE"]
INSCRICAO_ESTADUAL_EMITENTE = os.environ["INSCRICAO_ESTADUAL_EMITENTE"]
VALOR_FRETE = float(os.environ["VALOR_FRETE"])
PROCESSED_IDS_FILE = os.environ.get("PROCESSED_IDS_FILE", "processed_ids.txt")
SUBSCRIPTIONS_CREATED_AFTER = os.environ["SUBSCRIPTIONS_CREATED_AFTER"]

SUCCESS_RESPONSES_FILE = os.environ.get("SUCCESS_RESPONSES_FILE", "success_responses.json")
ERROR_RESPONSES_FILE = os.environ.get("ERROR_RESPONSES_FILE", "error_responses.json")
REQUEST_DELAY_SECONDS = 10
XML_OUTPUT_DIR = Path("xmls")

BRT = timezone(timedelta(hours=-3))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler("fiscal_notes.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ── JSON response files ────────────────────────────────────────────────────────

def _load_json_list(filepath: str) -> list:
    path = Path(filepath)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _append_to_json_file(filepath: str, entry: dict) -> None:
    records = _load_json_list(filepath)
    records.append(entry)
    Path(filepath).write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_success_response(subscription_id: str, response_data: dict) -> None:
    entry = {
        "subscription_id": subscription_id,
        "timestamp": datetime.now(BRT).isoformat(),
        "response": response_data,
    }
    _append_to_json_file(SUCCESS_RESPONSES_FILE, entry)
    logger.info("Resposta de sucesso salva em '%s'.", SUCCESS_RESPONSES_FILE)


def save_error_response(subscription_id: str, response_data: dict, http_status: int | None = None) -> None:
    entry = {
        "subscription_id": subscription_id,
        "timestamp": datetime.now(BRT).isoformat(),
        "http_status": http_status,
        "response": response_data,
    }
    _append_to_json_file(ERROR_RESPONSES_FILE, entry)
    logger.info("Resposta de erro salva em '%s'.", ERROR_RESPONSES_FILE)


def save_invoice_to_db(subscription_id: str, response_data: dict, emission_time: str) -> None:
    invoice_type = 0  # sempre "Sell"
    is_nfe = "requisicao_nota_fiscal" not in response_data

    sql = """
        INSERT INTO "Invoices" (
            "Id", "Type", "IsNfe", "IssueTimestamp", "Ref",
            "Status", "SefazStatus", "SefazMessage", "NfeKey",
            "Number", "Serie", "Protocol", "XmlPath", "DanfePath",
            "SubscriptionId", "CreatedAt", "UpdatedAt"
        ) VALUES (
            %(id)s, %(type)s, %(is_nfe)s, %(issue_timestamp)s, %(ref)s,
            %(status)s, %(sefaz_status)s, %(sefaz_message)s, %(nfe_key)s,
            %(number)s, %(serie)s, %(protocol)s, %(xml_path)s, %(danfe_path)s,
            %(subscription_id)s, %(created_at)s, %(updated_at)s
        )
    """
    now = datetime.now(BRT).isoformat()
    params = {
        "id": str(uuid.uuid4()),
        "type": invoice_type,
        "is_nfe": is_nfe,
        "issue_timestamp": emission_time,
        "ref": response_data.get("ref", subscription_id),
        "status": response_data.get("status", ""),
        "sefaz_status": response_data.get("status_sefaz", ""),
        "sefaz_message": response_data.get("mensagem_sefaz", ""),
        "nfe_key": response_data.get("chave_nfe", ""),
        "number": response_data.get("numero", ""),
        "serie": response_data.get("serie", ""),
        "protocol": response_data.get("protocolo", ""),
        "xml_path": response_data.get("caminho_xml_nota_fiscal", ""),
        "danfe_path": response_data.get("caminho_danfe", ""),
        "subscription_id": subscription_id,
        "created_at": now,
        "updated_at": now,
    }

    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        logger.info("Invoice inserida no banco com sucesso para Subscription %s.", subscription_id)
    except Exception as exc:
        logger.error("Erro ao inserir Invoice no banco para Subscription %s: %s", subscription_id, exc)
        conn.rollback()
    finally:
        conn.close()
        
        
# ── Download invoices ──────────────────────────────────────────────────────────────

def download_xml(subscription_id: str, response_data: dict) -> None:
    caminho_xml = response_data.get("caminho_xml_nota_fiscal")
    if not caminho_xml:
        logger.warning("Subscription %s — sem caminho_xml_nota_fiscal na resposta, download ignorado.", subscription_id)
        return

    url = f"{FOCUS_BASE_URL.rstrip('/')}{caminho_xml}"
    nome_arquivo = caminho_xml.rstrip("/").split("/")[-1]
    XML_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    destino = XML_OUTPUT_DIR / nome_arquivo

    if destino.exists():
        logger.info("XML já existe localmente, download ignorado: %s", destino)
        return

    for tentativa in range(1, 4):
        try:
            response = requests.get(url, auth=(FOCUS_TOKEN, ""), timeout=30)
            response.raise_for_status()
            destino.write_bytes(response.content)
            logger.info("XML baixado com sucesso (%s bytes): %s", f"{len(response.content):,}", destino)
            return
        except requests.HTTPError as exc:
            logger.warning("Erro HTTP ao baixar XML da Subscription %s: %s (tentativa %d/3)", subscription_id, exc, tentativa)
            if exc.response is not None and exc.response.status_code in (401, 403, 404):
                break
        except Exception as exc:
            logger.warning("Erro ao baixar XML da Subscription %s: %s (tentativa %d/3)", subscription_id, exc, tentativa)

        if tentativa < 3:
            time.sleep(tentativa * 2)

    logger.error("Falha definitiva no download do XML para Subscription %s — URL: %s", subscription_id, url)

# ── Processed IDs ──────────────────────────────────────────────────────────────

def load_processed_ids() -> set:
    records = _load_json_list(SUCCESS_RESPONSES_FILE)
    return {r["subscription_id"] for r in records if "subscription_id" in r}

# ── Database ───────────────────────────────────────────────────────────────────

def fetch_subscriptions() -> list[dict]:
    sql = """
        SELECT
            s."Id"               AS "SubscriptionId",
            s."AdditionalInfo"   AS "AdditionalInfo",
            e."Name"             AS "EventName",
            u."Name"             AS "CustomerName",
            u."Cpf"              AS "CustomerCpf",
            u."Phone"            AS "CustomerPhone",
            a."Street"           AS "CustomerStreet",
            a."HouseNumber"      AS "CustomerHouseNumber",
            a."Neighborhood"     AS "CustomerNeighborhood",
            a."City"             AS "CustomerCity",
            a."State"            AS "CustomerUf",
            a."ZipCode"          AS "CustomerCep",
            k."Price"            AS "KitPrice",
            k."Name"             AS "KitName",
            c."SubscriptionQuantity" AS "SubscriptionQuantity",
            p."Price"            AS "TotalPrice",
            p."Type"             AS "PaymentType",
            kob."Name"           AS "OrderBumpKitName",
            o."Price"          AS "OrderBumpKitPrice",
            eob."Name"           AS "OrderBumpEventName"
        FROM "Subscriptions" s
        JOIN "Payments"   p ON p."Id" = s."PaymentId"
        JOIN "Users"      u ON u."Id" = s."UserId"
        JOIN "Checkouts"  c ON c."Id" = s."CheckoutId"
        JOIN "Kits"       k ON k."Id" = c."KitId"
        JOIN "Events"     e ON e."Id" = k."EventId"
        join "ShippingQuotes" sq on sq."CheckoutId" = c."Id"
        JOIN "Addressess" a ON a."Id" = sq."AddressId"
        LEFT JOIN "OrderBumps" o   ON c."Id" = o."CheckoutId"
        LEFT JOIN "Kits"       kob ON o."KitId" = kob."Id"
        LEFT JOIN "Events"     eob ON eob."Id" = kob."EventId"
        WHERE COALESCE(s."UpdatedAt", s."CreatedAt") > %(created_after)s
        AND NOT (a."ZipCode" = '88804600' or a."ZipCode" = '88805090')   
        AND p."Status" = 1 
        ORDER BY s."Id";
    """
    
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, {"created_after": SUBSCRIPTIONS_CREATED_AFTER})
            raw_rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    return group_subscription_rows(raw_rows)


def group_subscription_rows(raw_rows: list[dict]) -> list[dict]:
    """
    The LEFT JOIN can produce multiple rows per subscription when there are
    multiple order bumps. This function collapses them into a single row per
    subscription, collecting all order bump names into a list.
    """
    grouped: dict[str, dict] = {}
    for row in raw_rows:
        sid = str(row["SubscriptionId"])
        if sid not in grouped:
            grouped[sid] = {
                **row,
                "OrderBumpKitNames": [],
                "OrderBumpKitPrices": [],
                "OrderBumpEventNames": [],
            }
        if row.get("OrderBumpKitName"):
            grouped[sid]["OrderBumpKitNames"].append(row["OrderBumpKitName"])
        if row.get("OrderBumpKitPrice") is not None:
            grouped[sid]["OrderBumpKitPrices"].append(float(row["OrderBumpKitPrice"]))
        # Event name is stored per order bump (None if the kit has no linked event)
        grouped[sid]["OrderBumpEventNames"].append(row.get("OrderBumpEventName"))
    return list(grouped.values())


# ── Helpers ────────────────────────────────────────────────────────────────────

def now_brt() -> str:
    return datetime.now(BRT).strftime("%Y-%m-%dT%H:%M:%S-03:00")


def resolve_kit_fields(kit_name: str) -> tuple[str, str]:
    if kit_name.strip().lower() == "kit medal":
        return "83062900", "100"
    return "61099000", "200"


def resolve_payment_fields(payment_type: int) -> tuple[str, str | None]:
    if payment_type == 0:
        return "99", None
    return "03", "2"


def build_item_description(
    kit_name: str,
    event_name: str,
    order_bump_names: list[str],
    order_bump_event_names: list[str | None],
    additional_info: str | None,
) -> str:
    """
    Builds the fiscal note item description combining kit name, order bump
    names (with their event name when available), and additional info,
    all separated by ' - '.

    Example without event:  "Kit Básico - Kit Medal - Tamanho M"
    Example with event:     "Kit Básico - Kit Medal (Corrida SP) - Tamanho M"
    """
    parts = [f"{kit_name.strip()} ({event_name.strip()})"]
    for ob_name, ob_event in zip(order_bump_names, order_bump_event_names):
        if ob_name and ob_name.strip():
            label = ob_name.strip()
            if ob_event and ob_event.strip():
                label = f"{label} ({ob_event.strip()})"
            parts.append(label)
    if additional_info and additional_info.strip():
        parts.append(additional_info.strip())
    return " - ".join(parts)


def get_package_weight(quantity: int, has_order_bump: bool, has_additional_info: bool) -> float:
    """
    Returns the package weight in kg.
    Base: 0.250 kg por unidade do kit principal (quantity)
    + 0.250 kg if there is an order bump
    + 0.250 kg if there is additional info
    """
    base_weight = 0.250 * quantity
    extra = (0.250 if has_order_bump else 0) + (0.250 if has_additional_info else 0)
    return round(base_weight + extra, 3)


# ── Note builders ──────────────────────────────────────────────────────────────

def build_nfce_body(row: dict) -> dict:
    valor_kit_principal = float(row["KitPrice"])
    valor_total = float(row["TotalPrice"])
    subscription_quantity = int(row.get("SubscriptionQuantity") or 1)
    order_bump_names: list[str] = row.get("OrderBumpKitNames", [])
    order_bump_prices: list[float] = row.get("OrderBumpKitPrices", [])
    additional_info: str | None = row.get("AdditionalInfo")
    has_order_bump = bool(order_bump_names)

    order_bump_event_names: list[str | None] = row.get("OrderBumpEventNames", [])

    # Sum of all kit prices (main + order bumps), considerando a quantidade do kit principal
    valor_order_bumps = sum(order_bump_prices)
    valor_produtos = round(valor_kit_principal * subscription_quantity + valor_order_bumps, 2)

    # Discount is the difference between gross product value + freight and what was actually charged
    valor_desconto_header = round(valor_produtos - valor_total + VALOR_FRETE, 2)

    ncm, codigo_produto = resolve_kit_fields(row["KitName"])
    forma_pagamento, tipo_integracao = resolve_payment_fields(int(row["PaymentType"]))

    descricao_principal = build_item_description(row["KitName"], row["EventName"], order_bump_names, order_bump_event_names, additional_info)

    # Build items list — main kit always present
    items = [
        {
            "numero_item": "1",
            "codigo_ncm": ncm,
            "codigo_produto": codigo_produto,
            "descricao": descricao_principal,
            "quantidade_comercial": subscription_quantity,
            "quantidade_tributavel": subscription_quantity,
            "cfop": "5102",
            "valor_unitario_comercial": valor_kit_principal,
            "valor_unitario_tributavel": valor_kit_principal,
            "valor_bruto": round(valor_kit_principal * subscription_quantity, 2),
            "valor_frete": VALOR_FRETE,
            "valor_desconto": valor_desconto_header,
            "unidade_comercial": "un",
            "unidade_tributavel": "un",
            "icms_origem": "0",
            "icms_situacao_tributaria": "102",
        }
    ]

    # Add one item per order bump
    for idx, (ob_name, ob_price) in enumerate(zip(order_bump_names, order_bump_prices), start=2):
        ob_ncm, ob_codigo = resolve_kit_fields(ob_name)
        items.append(
            {
                "numero_item": str(idx),
                "codigo_ncm": ob_ncm,
                "codigo_produto": ob_codigo,
                "descricao": ob_name.strip(),
                "quantidade_comercial": 1,
                "quantidade_tributavel": 1,
                "cfop": "5102",
                "valor_unitario_comercial": ob_price,
                "valor_unitario_tributavel": ob_price,
                "valor_bruto": ob_price,
                "valor_frete": 0,
                "valor_desconto": 0,
                "unidade_comercial": "un",
                "unidade_tributavel": "un",
                "icms_origem": "0",
                "icms_situacao_tributaria": "102",
            }
        )

    return {
        "cnpj_emitente": CNPJ_EMITENTE,
        "data_emissao": now_brt(),
        "modalidade_frete": "0",
        "local_destino": "1",
        "presenca_comprador": "4",
        "natureza_operacao": "VENDA AO CONSUMIDOR",
        "nome_destinatario": row["CustomerName"],
        "cpf_destinatario": row["CustomerCpf"],
        "indicador_inscricao_estadual_destinatario": 9,
        "logradouro_destinatario": row["CustomerStreet"],
        "numero_destinatario": row["CustomerHouseNumber"],
        "bairro_destinatario": row["CustomerNeighborhood"],
        "municipio_destinatario": row["CustomerCity"],
        "uf_destinatario": row["CustomerUf"],
        "cep_destinatario": row["CustomerCep"],
        "pais_destinatario": "Brasil",
        "telefone_destinatario": row["CustomerPhone"],
        "valor_frete": VALOR_FRETE,
        "valor_total": valor_total,
        "valor_produtos": valor_produtos,
        "valor_desconto": valor_desconto_header,
        "items": items,
        "formas_pagamento": [
            {
                "forma_pagamento": forma_pagamento,
                "valor_pagamento": valor_total,
                "tipo_integracao": tipo_integracao,
            }
        ],
    }


def build_nfe_body(row: dict) -> dict:
    valor_kit_principal = float(row["KitPrice"])
    valor_total = float(row["TotalPrice"])
    subscription_quantity = int(row.get("SubscriptionQuantity") or 1)
    order_bump_names: list[str] = row.get("OrderBumpKitNames", [])
    order_bump_prices: list[float] = row.get("OrderBumpKitPrices", [])
    additional_info: str | None = row.get("AdditionalInfo")
    has_order_bump = bool(order_bump_names)

    order_bump_event_names: list[str | None] = row.get("OrderBumpEventNames", [])

    # Sum of all kit prices (main + order bumps), considerando a quantidade do kit principal
    valor_order_bumps = sum(order_bump_prices)
    valor_produtos = round(valor_kit_principal * subscription_quantity + valor_order_bumps, 2)

    valor_desconto_header = round(valor_produtos - valor_total + VALOR_FRETE, 2)
    ncm, codigo_produto = resolve_kit_fields(row["KitName"])
    emission_time = now_brt()

    has_additional_info = bool(additional_info and additional_info.strip())
    peso = get_package_weight(subscription_quantity, has_order_bump, has_additional_info)
    descricao_principal = build_item_description(row["KitName"], row["EventName"], order_bump_names, order_bump_event_names, additional_info)

    # Build items list — main kit always present
    items = [
        {
            "numero_item": 1,
            "codigo_produto": codigo_produto,
            "descricao": descricao_principal,
            "cfop": "6102",
            "quantidade_comercial": subscription_quantity,
            "valor_unitario_comercial": valor_kit_principal,
            "unidade_comercial": "UN",
            "valor_bruto": round(valor_kit_principal * subscription_quantity, 2),
            "valor_frete": VALOR_FRETE,
            "valor_desconto": valor_desconto_header,
            "codigo_ncm": ncm,
            "inclui_no_total": 1,
            "icms_origem": 0,
            "icms_situacao_tributaria": "102",
            "pis_situacao_tributaria": "49",
            "cofins_situacao_tributaria": "49",
        }
    ]

    # Add one item per order bump
    for idx, (ob_name, ob_price) in enumerate(zip(order_bump_names, order_bump_prices), start=2):
        ob_ncm, ob_codigo = resolve_kit_fields(ob_name)
        items.append(
            {
                "numero_item": idx,
                "codigo_produto": ob_codigo,
                "descricao": ob_name.strip(),
                "cfop": "6102",
                "quantidade_comercial": 1,
                "valor_unitario_comercial": ob_price,
                "unidade_comercial": "UN",
                "valor_bruto": ob_price,
                "valor_frete": 0,
                "valor_desconto": 0,
                "codigo_ncm": ob_ncm,
                "inclui_no_total": 1,
                "icms_origem": 0,
                "icms_situacao_tributaria": "102",
                "pis_situacao_tributaria": "49",
                "cofins_situacao_tributaria": "49",
            }
        )

    return {
        "natureza_operacao": "Venda",
        "tipo_documento": 1,
        "finalidade_emissao": 1,
        "data_emissao": emission_time,
        "data_entrada_saida": emission_time,
        "local_destino": 2,
        "consumidor_final": 1,
        "presenca_comprador": 2,
        "cnpj_emitente": CNPJ_EMITENTE,
        "nome_emitente": NOME_EMITENTE,
        "nome_fantasia_emitente": NOME_FANTASIA_EMITENTE,
        "logradouro_emitente": LOGRADOURO_EMITENTE,
        "numero_emitente": NUMERO_EMITENTE,
        "bairro_emitente": BAIRRO_EMITENTE,
        "municipio_emitente": MUNICIPIO_EMITENTE,
        "uf_emitente": EMITTER_STATE,
        "cep_emitente": CEP_EMITENTE,
        "inscricao_estadual_emitente": INSCRICAO_ESTADUAL_EMITENTE,
        "regime_tributario_emitente": 1,
        "nome_destinatario": row["CustomerName"],
        "cpf_destinatario": row["CustomerCpf"],
        "indicador_inscricao_estadual_destinatario": 9,
        "logradouro_destinatario": row["CustomerStreet"],
        "numero_destinatario": row["CustomerHouseNumber"],
        "bairro_destinatario": row["CustomerNeighborhood"],
        "municipio_destinatario": row["CustomerCity"],
        "uf_destinatario": row["CustomerUf"],
        "cep_destinatario": row["CustomerCep"],
        "pais_destinatario": "Brasil",
        "telefone_destinatario": row["CustomerPhone"],
        "valor_frete": VALOR_FRETE,
        "valor_total": valor_total,
        "valor_produtos": valor_produtos,
        "modalidade_frete": 0,
        "valor_desconto": valor_desconto_header,
        "cnpj_transportador": "42584754008756",
        "nome_transportador": "J&T EXPRESS BRAZIL LTDA.",
        "inscricao_estadual_transportador": "261899228",
        "endereco_transportador": "Rua Joaquim Nabuco, 1971 - SAO LUIZ, 88803001",
        "municipio_transportador": "CRICIUMA",
        "uf_transportador": "SC",
        "volumes": [
            {
                "quantidade": 1,
                "especie": "PACOTE",
                "marca": "PADRÃO",
                "numero": "1",
                "peso_liquido": peso,
                "peso_bruto": peso,
            }
        ],
        "items": items,
    }


# ── API call ───────────────────────────────────────────────────────────────────

def emit_fiscal_note(subscription_id: str, is_nfce: bool, body: dict) -> dict:
    ref = str(subscription_id)

    if is_nfce:
        url = f"{FOCUS_BASE_URL}/nfce?ref={ref}&completa=1"
        note_type = "NFC-e"
    else:
        url = f"{FOCUS_BASE_URL}/nfe?ref={ref}"
        note_type = "NF-e"

    separator = "=" * 70
    logger.info(separator)
    logger.info(f"EMISSÃO {note_type}  |  Subscription ID: {ref}  |  URL: {url}")
    logger.info("REQUEST BODY:\n%s", json.dumps(body, ensure_ascii=False, indent=2))

    response = requests.post(
        url,
        json=body,
        auth=(FOCUS_TOKEN, ""),
        timeout=60,
    )

    try:
        response_data = response.json()
    except ValueError:
        response_data = {"raw_response": response.text}

    logger.info(
        "RESPONSE  |  Status HTTP: %s\n%s",
        response.status_code,
        json.dumps(response_data, ensure_ascii=False, indent=2),
    )
    logger.info(separator)

    if not response.ok:
        save_error_response(subscription_id, response_data, http_status=response.status_code)
        response.raise_for_status()

    fiscal_status = response_data.get("status", "")
    if fiscal_status != "autorizado":
        sefaz_msg = response_data.get("mensagem_sefaz", "sem mensagem")
        sefaz_code = response_data.get("status_sefaz", "?")
        save_error_response(subscription_id, response_data, http_status=response.status_code)
        raise ValueError(
            f"SEFAZ rejeitou a nota — status: '{fiscal_status}' | "
            f"código: {sefaz_code} | mensagem: {sefaz_msg}"
        )

    return response_data


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    processed_ids = load_processed_ids()
    logger.info("IDs já processados carregados: %d", len(processed_ids))

    rows = fetch_subscriptions()
    logger.info("Registros encontrados no banco: %d", len(rows))

    success_count = 0
    error_count = 0
    skipped_count = 0

    pending_rows = [r for r in rows if str(r["SubscriptionId"]) not in processed_ids]
    skipped_count = len(rows) - len(pending_rows)

    for index, row in enumerate(pending_rows):
        subscription_id = str(row["SubscriptionId"])

        if index > 0:
            logger.info("Aguardando %ds antes da próxima requisição…", REQUEST_DELAY_SECONDS)
            time.sleep(REQUEST_DELAY_SECONDS)

        recipient_state = (row["CustomerUf"] or "").strip().upper()
        is_nfce = recipient_state == EMITTER_STATE.strip().upper()

        body = build_nfce_body(row) if is_nfce else build_nfe_body(row)
        emission_time = body["data_emissao"]  # captura antes da chamada

        try:
            response_data = emit_fiscal_note(subscription_id, is_nfce, body)
            save_invoice_to_db(subscription_id, response_data, emission_time)
            processed_ids.add(subscription_id)
            download_xml(subscription_id, response_data)
            success_count += 1
        except requests.HTTPError as exc:
            logger.error("Falha HTTP na Subscription %s: %s", subscription_id, exc)
            error_count += 1
        except Exception as exc:
            logger.error("Erro inesperado na Subscription %s: %s", subscription_id, exc)
            error_count += 1

    logger.info(
        "Concluído — Sucesso: %d | Erros: %d | Pulados: %d",
        success_count,
        error_count,
        skipped_count,
    )


if __name__ == "__main__":
    main()