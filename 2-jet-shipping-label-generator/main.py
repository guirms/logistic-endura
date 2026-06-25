import hashlib
import base64
import json
from pathlib import Path
import requests
import time
import os
import glob
import random
import string
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
from dotenv import load_dotenv
import psycopg2

SCRIPT_DIR = Path(__file__).parent

load_dotenv(dotenv_path=SCRIPT_DIR.parent / ".env")

API_ACCOUNT   = os.getenv("API_ACCOUNT")
PRIVATE_KEY   = os.getenv("PRIVATE_KEY")
CUSTOMER_CODE = os.getenv("CUSTOMER_CODE")
BODY_DIGEST   = os.getenv("BODY_DIGEST")

URL            = "https://openapi.jtjms-br.com/webopenplatformapi/api/order/addOrder"
SUCCESS_XMLS = SCRIPT_DIR.parent / "1-sell-invoice-generator" / "success_responses.json"
PASTA_XMLS = SCRIPT_DIR.parent / "1-sell-invoice-generator" / "xmls"
INTERVALO_SEG  = 10
SUCCESS_FILE   = SCRIPT_DIR / "success_responses.json"
ERROR_FILE     = SCRIPT_DIR / "error_responses.json" 

DB_HOST     = os.getenv("DB_HOST")
DB_PORT     = os.getenv("DB_PORT")
DB_NAME     = os.getenv("DB_NAME")
DB_USER     = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

SENDER = {
    "Name": "Endura Run",
    "PostCode": "88804600",
    "MailBox": "guilhermesantana84@hotmail.com",
    "TaxNumber": "59173759000119",
    "Mobile": "48991156679",
    "Phone": "48991156679",
    "Prov": "SC",
    "City": "Criciuma",
    "Street": "Rua Imigrante de Lucca",
    "StreetNumber": "855",
    "Address": "Rua Imigrante de Lucca, Pinheirinho, 855",
    "AreaCode": "48",
    "IeNumber": "262521504",
    "Area": "Pinheirinho",
    "Company": "Guilherme Machado Santana",
}


def gerar_digest(biz_content_str: str) -> str:
    raw = biz_content_str + PRIVATE_KEY
    md5_bytes = hashlib.md5(raw.encode("utf-8")).digest()
    return base64.b64encode(md5_bytes).decode("utf-8")


def get_timestamp() -> str:
    return str(int(time.time() * 1000))


def gerar_txlogistic_id(tamanho: int = 10) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=tamanho))


def extrair_ddd(telefone: str) -> str:
    digitos = "".join(filter(str.isdigit, telefone))
    if digitos.startswith("55") and len(digitos) >= 4:
        return digitos[2:4]
    if len(digitos) >= 2:
        return digitos[:2]
    return "00"


def formatar_data_iso(data_str: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(data_str[:19], fmt[:19])
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return data_str


def parsear_xml(caminho: str) -> dict:
    tree = ET.parse(caminho)
    root = tree.getroot()

    infNFe = root.find(".//nfe:infNFe", NS)
    ide    = infNFe.find("nfe:ide", NS)
    dest   = infNFe.find("nfe:dest", NS)
    ender  = dest.find("nfe:enderDest", NS)
    det    = infNFe.find("nfe:det", NS)
    prod   = det.find("nfe:prod", NS)
    total  = infNFe.find(".//nfe:ICMSTot", NS)
    chNFe  = infNFe.get("Id", "").replace("NFe", "")

    nNF              = ide.findtext("nfe:nNF", default="", namespaces=NS).zfill(9)
    serie            = ide.findtext("nfe:serie", default="1", namespaces=NS)
    dhEmi            = ide.findtext("nfe:dhEmi", default="", namespaces=NS)
    v_nf             = total.findtext("nfe:vNF", default="0.00", namespaces=NS)

    nome_dest        = dest.findtext("nfe:xNome", default="", namespaces=NS)
    cpf_cnpj         = dest.findtext("nfe:CPF", default="", namespaces=NS) or dest.findtext("nfe:CNPJ", default="", namespaces=NS)
    fone_dest        = ender.findtext("nfe:fone", default="", namespaces=NS)
    cep_dest         = ender.findtext("nfe:CEP", default="", namespaces=NS)
    uf_dest          = ender.findtext("nfe:UF", default="", namespaces=NS)
    cidade_dest      = ender.findtext("nfe:xMun", default="", namespaces=NS)
    rua_dest         = ender.findtext("nfe:xLgr", default="", namespaces=NS)
    numero_dest      = ender.findtext("nfe:nro", default="", namespaces=NS)
    bairro_dest      = ender.findtext("nfe:xBairro", default="", namespaces=NS)

    xProd            = prod.findtext("nfe:xProd", default="Produto", namespaces=NS)
    v_prod           = prod.findtext("nfe:vProd", default="0.00", namespaces=NS)
    pesoL_raw        = prod.findtext("nfe:pesoL", default="", namespaces=NS)

    # Normalize weight: use default if missing, convert comma to dot, format to 2 decimals
    if not pesoL_raw:
        pesoL = "0.29"
    else:
        pesoL_candidate = pesoL_raw.replace(",", ".").strip()
        try:
            pesoL = f"{float(pesoL_candidate):.2f}"
        except Exception:
            pesoL = "0.29"

    ddd = extrair_ddd(fone_dest)

    address_dest = (
        f"{rua_dest}, {numero_dest} - {bairro_dest}, "
        f"{cidade_dest} - {uf_dest}, {cep_dest}"
    )

    return {
        "nNF": nNF,
        "serie": serie,
        "dhEmi": formatar_data_iso(dhEmi),
        "vNF": v_nf,
        "chNFe": chNFe,
        "dest": {
            "Name": nome_dest,
            "PostCode": cep_dest,
            "MailBox": "guilhermesantana84@hotmail.com",
            "TaxNumber": cpf_cnpj,
            "Mobile": fone_dest,
            "Phone": fone_dest,
            "Prov": uf_dest,
            "City": cidade_dest,
            "Street": rua_dest,
            "StreetNumber": numero_dest,
            "Address": address_dest,
            "AreaCode": ddd,
            "IeNumber": "0000000",
            "Area": bairro_dest,
            "Company": None,
        },
        "item": {
            "ItemName": xProd,
            "ItemValue": v_prod,
        },
        "pesoL": pesoL,
    }


def montar_payload(dados: dict) -> dict:
    tx_id = gerar_txlogistic_id(10)

    return {
        "TxlogisticId":        tx_id,
        "ExpressType":         "EZ",
        "OrderType":           "2",
        "ServiceType":         "02",
        "DeliveryType":        "03",
        "GoodsType":           "bm000006",
        "Weight":              dados.get("pesoL", "0.29"),
        "TotalQuantity":       1,
        "InvoiceNumber":       dados["nNF"],
        "InvoiceSerialNumber": dados["serie"],
        "InvoiceMoney":        dados["vNF"],
        "TaxCode":             tx_id,
        "InvoiceIssueDate":    dados["dhEmi"],
        "InvoiceAccessKey":    dados["chNFe"],
        "Sender":              SENDER,
        "Receiver":            dados["dest"],
        "Translate":           SENDER,
        "Items": [
            {
                "ItemType":      "bm000006",
                "ItemName":      dados["item"]["ItemName"],
                "ItemValue":     dados["item"]["ItemValue"],
                "PriceCurrency": "BRL",
                "Desc":          "Kit de evento de corrida",
            }
        ],
        "CustomerCode": CUSTOMER_CODE,
        "Digest":       BODY_DIGEST,
    }


def enviar_pedido(payload: dict) -> dict:
    biz_content_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    digest          = gerar_digest(biz_content_str)
    timestamp       = get_timestamp()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "apiAccount":   API_ACCOUNT,
        "digest":       digest,
        "timestamp":    timestamp,
    }

    response = requests.post(
        URL,
        data={"bizContent": biz_content_str},
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def carregar_json(caminho: str) -> list:
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def salvar_json(caminho: str, dados: list) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)


def carregar_chaves_processadas(sucessos: list) -> set:
    return {entry["chNFe"] for entry in sucessos if "chNFe" in entry}

def carregar_mapa_subscription(caminho: Path) -> dict:
    """Monta um dict {chNFe: subscription_id} a partir do success_responses do script 1."""
    if not caminho.exists():
        return {}
    try:
        registros = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    mapa = {}
    for entry in registros:
        chave_nfe = entry.get("response", {}).get("chave_nfe", "")
        sub_id = entry.get("subscription_id")
        if chave_nfe and sub_id:
            mapa[chave_nfe.replace("NFe", "")] = sub_id
    return mapa


def atualizar_tracking_code(subscription_id: str, bill_code: str) -> None:
    sql = 'UPDATE "Subscriptions" SET "TrackingCode" = %(tracking_code)s WHERE "Id" = %(subscription_id)s'
    try:
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, {"tracking_code": bill_code, "subscription_id": subscription_id})
            conn.commit()
            print(f"    ✅ TrackingCode '{bill_code}' salvo no banco para Subscription {subscription_id}.")
        except Exception as exc:
            conn.rollback()
            print(f"    ❌ Erro ao salvar TrackingCode no banco para Subscription {subscription_id}: {exc}")
        finally:
            conn.close()
    except Exception as exc:
        print(f"    ❌ Erro de conexão ao banco para Subscription {subscription_id}: {exc}")
        
def main():
    arquivos = sorted(glob.glob(os.path.join(PASTA_XMLS, "*.xml")))
    total    = len(arquivos)

    if total == 0:
        print(f"❌ Nenhum XML encontrado na pasta '{PASTA_XMLS}/'")
        return

    sucessos  = carregar_json(SUCCESS_FILE)
    erros     = carregar_json(ERROR_FILE)
    ja_feitos = carregar_chaves_processadas(sucessos)

    pendentes = total - len(ja_feitos)

    print(f"\n📋 {total} XMLs encontrados em '{PASTA_XMLS}/'")
    if ja_feitos:
        print(f"⏭️  {len(ja_feitos)} já processados anteriormente — serão ignorados")
    print(f"📬 {pendentes} pedidos a enviar")
    print(f"⏱  Intervalo entre requisições: {INTERVALO_SEG}s")
    estimado = pendentes * INTERVALO_SEG
    print(f"⏳ Tempo estimado: ~{estimado // 60}min {estimado % 60}s\n")

    enviados_agora = 0
    
    mapa_subscriptions = carregar_mapa_subscription(SUCCESS_XMLS)
 
    for i, caminho in enumerate(arquivos, start=1):
        nome_arquivo = os.path.basename(caminho)
        print(f"[{i:>3}/{total}] {nome_arquivo} ...", end=" ", flush=True)

        entrada_log = {"arquivo": nome_arquivo, "timestamp": datetime.now(timezone.utc).isoformat()}

        try:
            dados = parsear_xml(caminho)

            if dados["chNFe"] in ja_feitos:
                print(f"⏭️  Já processado (chNFe: {dados['chNFe'][:20]}...) — pulando")
                continue
            payload = montar_payload(dados)
            resposta = enviar_pedido(payload)

            entrada_log["TxlogisticId"] = payload["TxlogisticId"]
            entrada_log["chNFe"]        = dados["chNFe"]
            entrada_log["nNF"]          = dados["nNF"]
            entrada_log["destinatario"] = dados["dest"]["Name"]
            entrada_log["resposta"]     = resposta

            cod = resposta.get("code") or resposta.get("responseCode") or ""
            msg = resposta.get("message") or resposta.get("responseMessage") or ""

            if str(cod) in ("1", "200", "true", "True") or resposta.get("success"):
                print(f"✅  TxlogisticId: {payload['TxlogisticId']} | NF: {dados['nNF']} | {dados['dest']['Name']}")

                try:
                    bill_code = resposta["data"]["orderList"][0]["billCode"]
                    sub_id = mapa_subscriptions.get(dados["chNFe"])
                    if sub_id:
                        atualizar_tracking_code(sub_id, bill_code)
                    else:
                        print(f"    ⚠️  Subscription não encontrada para chNFe {dados['chNFe'][:20]}...")
                except (KeyError, IndexError, TypeError) as exc:
                    print(f"    ⚠️  Não foi possível extrair billCode da resposta: {exc}")

                sucessos.append(entrada_log)
                ja_feitos.add(dados["chNFe"])
                enviados_agora += 1
                salvar_json(SUCCESS_FILE, sucessos)
            else:
                print(f"⚠️   Resposta inesperada — código: {cod} | mensagem: {msg}")
                entrada_log["motivo_erro"] = f"código: {cod} | mensagem: {msg}"
                erros.append(entrada_log)
                salvar_json(ERROR_FILE, erros)

        except ET.ParseError as e:
            print(f"❌ XML inválido — {e}")
            entrada_log["motivo_erro"] = f"XML inválido: {e}"
            erros.append(entrada_log)
            salvar_json(ERROR_FILE, erros)

        except requests.exceptions.RequestException as e:
            print(f"❌ Erro de rede — {e}")
            entrada_log["motivo_erro"] = f"Erro de rede: {e}"
            erros.append(entrada_log)
            salvar_json(ERROR_FILE, erros)

        except Exception as e:
            print(f"❌ Erro inesperado — {e}")
            entrada_log["motivo_erro"] = f"Erro inesperado: {e}"
            erros.append(entrada_log)
            salvar_json(ERROR_FILE, erros)

        if i < total:
            time.sleep(INTERVALO_SEG)

    print(f"\n✅ Concluído! {len(sucessos)} sucessos no total ({enviados_agora} nesta execução), {len(erros)} erros.")
    print(f"   📄 Sucessos → {SUCCESS_FILE}")
    print(f"   📄 Erros    → {ERROR_FILE}\n")


if __name__ == "__main__":
    main()