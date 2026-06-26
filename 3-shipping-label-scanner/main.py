"""
Scanner de Pedidos — v3
Layout profissional para uso em galpão/expedição.
Imagens carregadas de caminhos relativos ao diretório do script.
Configuração via .env na mesma pasta.

Melhorias v3:
- Tela de loading animada durante a busca (substitui o resultado atual)
- Banner de alerta laranja chamativo quando há order bumps
- Banner de alerta azul chamativo quando há informações adicionais
"""

import tkinter as tk
import psycopg2
import psycopg2.extras
import os
import threading
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image, ImageTk, ImageDraw
import sys
import json
from datetime import datetime, timezone

load_dotenv()

def resource_path(relative_path=""):
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / relative_path
    return Path(__file__).parent.resolve() / relative_path

BASE_DIR = resource_path()

SUCCESS_FILE = BASE_DIR / "success_responses.json"
ERROR_FILE   = BASE_DIR / "error_responses.json"

# ── Banco ─────────────────────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASSWORD")

SQL = """
    SELECT
        k."Name"           AS "KitName",
        k."PngPicture"     AS "KitPngPicture",
        e."Name"           AS "EventName",
        s."TShirtSize"     AS "TShirtSize",
        s."AdditionalInfo" AS "AdditionalInfo",
        c."SubscriptionQuantity" AS "SubscriptionQuantity",
        kob."Name"         AS "OrderBumpKitName",
        kob."PngPicture"   AS "OrderBumpKitPngPicture",
        eob."Name"         AS "OrderBumpEventName"
    FROM "Subscriptions" s
    JOIN "Payments"   p   ON p."Id"  = s."PaymentId"
    JOIN "Checkouts"  c   ON c."Id"  = s."CheckoutId"
    JOIN "Kits"       k   ON k."Id"  = c."KitId"
    JOIN "Events"     e   ON e."Id"  = k."EventId"
    LEFT JOIN "OrderBumps" o   ON c."Id"  = o."CheckoutId"
    LEFT JOIN "Kits"       kob ON o."KitId" = kob."Id"
    LEFT JOIN "Events"     eob ON eob."Id"  = kob."EventId"
    WHERE s."TrackingCode" = %s
      AND p."Status" = 1;
"""

# ── Paleta ────────────────────────────────────────────────────────────────
BG         = "#F5F4F0"
SURFACE    = "#FFFFFF"
SURFACE2   = "#EDECEA"
BORDER     = "#D6D3CE"
BORDER2    = "#C4C0BB"
INK        = "#1A1916"
INK2       = "#6B6760"
INK3       = "#A09D99"
GREEN      = "#1D7A4F"
GREEN_BG   = "#E8F5EE"
RED        = "#B83232"
RED_BG     = "#FAEAEA"
ORANGE     = "#C27318"
ORANGE_BG  = "#FDF3E3"
DIVIDER    = "#E0DDD9"

# Cores dos alertas chamativo
ALERT_BUMP_BG     = "#FF6B00"   # laranja forte
ALERT_BUMP_FG     = "#FFFFFF"
ALERT_BUMP_BORDER = "#CC5500"
ALERT_INFO_BG     = "#1565C0"   # azul forte
ALERT_INFO_FG     = "#FFFFFF"
ALERT_INFO_BORDER = "#0D47A1"

IMG_KIT_W  = 220
IMG_KIT_H  = 220
IMG_BUMP_W = 160
IMG_BUMP_H = 160

TSHIRT_SIZES = {"0": "P", "1": "M", "2": "G", "3": "GG", "4": "XG"}

# ── Utilitários de imagem ──────────────────────────────────────────────────
def load_image(relative_path, w, h):
    if not relative_path or not relative_path.strip():
        return None
    full = BASE_DIR / relative_path.strip().lstrip("/\\")
    if not full.exists():
        return None
    try:
        img = Image.open(full).convert("RGBA")
        img.thumbnail((w, h), Image.LANCZOS)
        canvas = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        x = (w - img.width) // 2
        y = (h - img.height) // 2
        canvas.paste(img, (x, y), img.split()[3])
        bg = Image.new("RGB", (w, h), (255, 255, 255))
        bg.paste(canvas, mask=canvas.split()[3])
        return ImageTk.PhotoImage(bg)
    except Exception:
        return None


def placeholder(w, h):
    img = Image.new("RGB", (w, h), color="#EDECEA")
    draw = ImageDraw.Draw(img)
    draw.text((w // 2, h // 2), "SEM\nIMAGEM", fill="#C4C0BB",
              anchor="mm", align="center")
    return ImageTk.PhotoImage(img)


# ── Banco ──────────────────────────────────────────────────────────────────
def buscar_pedido(codigo):
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
        connect_timeout=6,
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL, (codigo.strip(),))
            rows = cur.fetchall()
            # DEBUG — remova após confirmar que os dados chegam corretos
            print(f"\n=== DEBUG: {len(rows)} row(s) para '{codigo}' ===")
            for i, r in enumerate(rows):
                print(f"  row[{i}]:")
                print(f"    KitName              = {r['KitName']!r}")
                print(f"    KitPngPicture        = {r['KitPngPicture']!r}")
                print(f"    EventName            = {r['EventName']!r}")
                print(f"    OrderBumpKitName     = {r['OrderBumpKitName']!r}")
                print(f"    OrderBumpKitPngPicture = {r['OrderBumpKitPngPicture']!r}")
                print(f"    OrderBumpEventName   = {r['OrderBumpEventName']!r}")
            print("=" * 50)
            return rows
    finally:
        conn.close()
        
def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _append_json(path: Path, entry: dict) -> None:
    records = _load_json(path)
    records.append(entry)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def salvar_sucesso(code: str, rows: list) -> None:
    row = rows[0]
    _append_json(SUCCESS_FILE, {
        "tracking_code": code,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kit": str(row.get("KitName", "")),
        "event": str(row.get("EventName", "")),
        "total_items": len(rows),
    })


def salvar_erro(code: str, motivo: str) -> None:
    _append_json(ERROR_FILE, {
        "tracking_code": code,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "motivo": motivo,
    })


def atualizar_shipping_status(code: str) -> None:
    sql = """
        UPDATE "Subscriptions"
        SET "ShippingStatus" = 1, "UpdatedAt" = %(updated_at)s
        WHERE "TrackingCode" = %(tracking_code)s
    """
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT,
            dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            connect_timeout=6,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(sql, {"updated_at": datetime.now(timezone.utc), "tracking_code": code})
            conn.commit()
        except Exception as exc:
            conn.rollback()
            salvar_erro(code, f"Erro ao atualizar banco: {exc}")
        finally:
            conn.close()
    except Exception as exc:
        salvar_erro(code, f"Erro de conexão: {exc}")


# ── Widget: Tag badge ──────────────────────────────────────────────────────
class Tag(tk.Frame):
    def __init__(self, parent, text, fg, bg, **kw):
        super().__init__(parent, bg=bg, bd=0, **kw)
        tk.Label(self, text=text, bg=bg, fg=fg,
                 font=("Helvetica", 8, "bold"),
                 padx=8, pady=3).pack()


# ── Widget: Banner de alerta chamativo ────────────────────────────────────
class AlertBanner(tk.Frame):
    """Banner horizontal de alta visibilidade para order bumps e info adicional."""

    def __init__(self, parent, icon, title, subtitle, bg_color, fg_color,
                 border_color, **kw):
        super().__init__(
            parent,
            bg=bg_color,
            highlightthickness=3,
            highlightbackground=border_color,
            **kw,
        )

        inner = tk.Frame(self, bg=bg_color, padx=22, pady=14)
        inner.pack(fill="x")

        # Ícone grande à esquerda
        tk.Label(inner, text=icon, bg=bg_color, fg=fg_color,
                 font=("Helvetica", 28)).pack(side="left", padx=(0, 16))

        # Textos
        text_col = tk.Frame(inner, bg=bg_color)
        text_col.pack(side="left", fill="x", expand=True)

        tk.Label(text_col, text=title, bg=bg_color, fg=fg_color,
                 font=("Helvetica", 13, "bold"),
                 anchor="w", justify="left").pack(anchor="w")

        if subtitle:
            tk.Label(text_col, text=subtitle, bg=bg_color, fg=fg_color,
                     font=("Helvetica", 10),
                     anchor="w", justify="left",
                     wraplength=780).pack(anchor="w", pady=(3, 0))

        # Seta indicativa à direita
        tk.Label(inner, text="▼  VER ABAIXO", bg=bg_color, fg=fg_color,
                 font=("Helvetica", 9, "bold")).pack(side="right", padx=(16, 0))


# ── Widget: Painel de kit ──────────────────────────────────────────────────
class KitPanel(tk.Frame):
    _refs = []

    def __init__(self, parent, kit_name, img_path, tag_text, tag_fg, tag_bg,
             event_name=None, is_main=False, index=None, tshirt="", quantity="1", **kw):
        quantity = str(quantity)
        super().__init__(parent, bg=SURFACE, bd=0,
                        highlightthickness=1, highlightbackground=BORDER, **kw)

        iw = IMG_KIT_W if is_main else IMG_BUMP_W
        ih = IMG_KIT_H if is_main else IMG_BUMP_H

        # ── Imagem (esquerda) ──────────────────────────────────────────────
        left = tk.Frame(self, bg=SURFACE2, width=iw + 2, height=ih + 2)
        left.pack(side="left")
        left.pack_propagate(False)
        photo = load_image(img_path, iw, ih) or placeholder(iw, ih)
        KitPanel._refs.append(photo)
        tk.Label(left, image=photo, bg=SURFACE2, bd=0).pack(expand=True)

        # Divisor vertical
        tk.Frame(self, bg=BORDER, width=1).pack(side="left", fill="y")

        # ── Info (direita) ─────────────────────────────────────────────────
        right = tk.Frame(self, bg=SURFACE, padx=22, pady=18)
        right.pack(side="left", fill="both", expand=True)

        # Linha de badges: tipo + numerador
        badge_row = tk.Frame(right, bg=SURFACE)
        badge_row.pack(anchor="w")
        Tag(badge_row, text=tag_text, fg=tag_fg, bg=tag_bg).pack(side="left")
        if index is not None:
            Tag(badge_row, text=f"ITEM {index}", fg=INK3, bg=BG).pack(
                side="left", padx=(6, 0))

        # EVENTO — destaque principal
        ev = event_name or "—"
        tk.Label(right, text=ev,
                 bg=SURFACE, fg=INK,
                 font=("Georgia", 20 if is_main else 17, "bold"),
                 wraplength=420, justify="left", anchor="w").pack(
                     anchor="w", pady=(10, 0))

        # Kit name — subtítulo
        tk.Label(right, text=kit_name or "—",
         bg=SURFACE, fg=INK2,
         font=("Helvetica", 11),
         wraplength=420, justify="left", anchor="w").pack(
             anchor="w", pady=(4, 0))

        # Tamanho de camiseta ← novo bloco
        if "tshirt":
            ts_row = tk.Frame(right, bg=SURFACE)
            ts_row.pack(anchor="w", pady=(10, 0))
            tk.Label(ts_row, text="CAMISETA", bg=SURFACE, fg=INK3,
                    font=("Helvetica", 8, "bold")).pack(side="left", padx=(0, 8))
            tk.Label(ts_row, text=tshirt, bg=SURFACE, fg=INK,
                    font=("Helvetica", 22, "bold")).pack(side="left")
            
        qty_row = tk.Frame(right, bg=SURFACE)
        qty_row.pack(anchor="w", pady=(10, 0))
        tk.Label(qty_row, text="QUANTIDADE DE KITS", bg=SURFACE, fg=INK3,
                font=("Helvetica", 8, "bold")).pack(side="left", padx=(0, 8))
        qty_frame = tk.Frame(qty_row, bg=RED if int(quantity) > 1 else SURFACE,
                            highlightthickness=2 if int(quantity) > 1 else 0,
                            highlightbackground=RED)
        qty_frame.pack(side="left")
        tk.Label(qty_frame, text=quantity, bg=RED if int(quantity) > 1 else SURFACE,
                fg=SURFACE if int(quantity) > 1 else INK,
         font=("Helvetica", 22, "bold"),
         padx=10 if int(quantity) > 1 else 0).pack()

        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=12)

        ctx = "Kit principal do pedido" if is_main else "Item adicional incluso no pacote"
        tk.Label(right, text=ctx, bg=SURFACE, fg=INK3,
                 font=("Helvetica", 9), anchor="w").pack(anchor="w")


# ── Widget: Tela de Loading ────────────────────────────────────────────────
class LoadingScreen(tk.Frame):
    """Tela de loading animada exibida durante a consulta ao banco."""

    _DOTS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    _MSGS = [
        "Consultando banco de dados…",
        "Buscando pedido…",
        "Verificando pagamento…",
        "Carregando informações…",
    ]

    def __init__(self, parent, code, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._job = None
        self._dot_idx = 0
        self._msg_idx = 0
        self._msg_tick = 0

        # ── Conteúdo centralizado ──────────────────────────────────────────
        center = tk.Frame(self, bg=BG)
        center.place(relx=0.5, rely=0.45, anchor="center")

        # Spinner (braille animado)
        self.spinner_lbl = tk.Label(
            center, text=self._DOTS[0],
            bg=BG, fg=INK,
            font=("Courier", 52, "bold"),
        )
        self.spinner_lbl.pack()

        # Código sendo buscado
        tk.Label(center, text="BUSCANDO", bg=BG, fg=INK3,
                 font=("Helvetica", 9, "bold")).pack(pady=(18, 4))
        tk.Label(center, text=code, bg=BG, fg=INK,
                 font=("Courier", 26, "bold")).pack()

        # Mensagem rotativa
        self.msg_lbl = tk.Label(
            center, text=self._MSGS[0],
            bg=BG, fg=INK3,
            font=("Helvetica", 10),
        )
        self.msg_lbl.pack(pady=(14, 0))

        self._animate()

    def _animate(self):
        self._dot_idx = (self._dot_idx + 1) % len(self._DOTS)
        self.spinner_lbl.config(text=self._DOTS[self._dot_idx])

        self._msg_tick += 1
        if self._msg_tick % 6 == 0:   # troca mensagem a cada ~600 ms
            self._msg_idx = (self._msg_idx + 1) % len(self._MSGS)
            self.msg_lbl.config(text=self._MSGS[self._msg_idx])

        self._job = self.after(100, self._animate)

    def stop(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None


# ── Aplicação ──────────────────────────────────────────────────────────────
class ScannerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Expedição — Scanner de Pedidos")
        self.configure(bg=BG)
        self.geometry("1080x800")
        self.minsize(860, 560)
        self._blink_job = None
        self._scan_count = 0
        self._loading_screen = None

        self._build_topbar()
        self._build_input_zone()
        self._build_result_area()
        self._show_idle()
        self.after(100, self.entry.focus_set)

    # ── Top bar ────────────────────────────────────────────────────────────
    def _build_topbar(self):
        bar = tk.Frame(self, bg=INK, height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="EXPEDIÇÃO", bg=INK, fg=SURFACE,
                 font=("Helvetica", 11, "bold"), padx=28).pack(side="left")
        tk.Frame(bar, bg="#333", width=1).pack(side="left", fill="y", pady=12)
        tk.Label(bar, text="SCANNER DE PEDIDOS", bg=INK, fg="#888",
                 font=("Helvetica", 9), padx=16).pack(side="left")

        self.counter_lbl = tk.Label(bar, text="0 scans realizados",
                                    bg=INK, fg="#666",
                                    font=("Helvetica", 9), padx=24)
        self.counter_lbl.pack(side="right")

        self.dot = tk.Label(bar, text="●", bg=INK, fg="#444",
                            font=("Helvetica", 14), padx=8)
        self.dot.pack(side="right")

    # ── Input ──────────────────────────────────────────────────────────────
    def _build_input_zone(self):
        zone = tk.Frame(self, bg=SURFACE)
        zone.pack(fill="x")

        tk.Frame(zone, bg=BORDER, height=1).pack(fill="x")

        inner = tk.Frame(zone, bg=SURFACE, padx=28, pady=16)
        inner.pack(fill="x")

        tk.Label(inner, text="CÓDIGO DE RASTREIO", bg=SURFACE, fg=INK3,
                 font=("Helvetica", 8, "bold")).pack(side="left",
                                                      anchor="s", padx=(0, 12))

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            inner, textvariable=self.entry_var,
            bg=SURFACE, fg=INK, insertbackground=INK,
            font=("Courier", 22, "bold"),
            bd=0, highlightthickness=0, relief="flat", width=28,
        )
        self.entry.pack(side="left", ipady=4)
        self.entry.bind("<Return>", self._on_scan)
        self.entry.bind("<KP_Enter>", self._on_scan)
        self.entry.bind("<KeyRelease>", lambda _: self.status_lbl.config(text="", fg=INK3))

        self.btn = tk.Button(
            inner, text="BUSCAR  →",
            bg=INK, fg=SURFACE,
            activebackground="#333", activeforeground=SURFACE,
            font=("Helvetica", 9, "bold"),
            bd=0, relief="flat", cursor="hand2",
            padx=20, pady=8, command=self._on_scan,
        )
        self.btn.pack(side="left", padx=(16, 0))

        status_bar = tk.Frame(zone, bg=SURFACE, padx=28)
        status_bar.pack(fill="x")
        self.status_lbl = tk.Label(status_bar, text="", bg=SURFACE, fg=INK3,
                                   font=("Helvetica", 9))
        self.status_lbl.pack(side="left")

        tk.Frame(zone, bg=BORDER, height=1).pack(fill="x")

    # ── Área de resultado ──────────────────────────────────────────────────
    def _build_result_area(self):
        self.canvas = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0)
        vsb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.result_frame = tk.Frame(self.canvas, bg=BG)
        self._cwin = self.canvas.create_window((0, 0), window=self.result_frame,
                                                anchor="nw")
        self.result_frame.bind("<Configure>",
            lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self._cwin, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-1 * (e.delta // 120), "units"))

    # ── Idle ───────────────────────────────────────────────────────────────
    def _show_idle(self):
        self._clear()
        pad = tk.Frame(self.result_frame, bg=BG)
        pad.pack(expand=True, pady=100)
        tk.Label(pad, text="▌▌ ▌▌▌ ▌ ▌▌▌ ▌▌ ▌▌▌▌ ▌",
                 bg=BG, fg=BORDER2, font=("Courier", 28, "bold")).pack()
        tk.Label(pad, text="Aguardando leitura do código de barras",
                 bg=BG, fg=INK3, font=("Helvetica", 12)).pack(pady=(14, 4))
        tk.Label(pad, text="Escaneie ou digite o código e pressione Enter",
                 bg=BG, fg=INK3, font=("Helvetica", 9)).pack()

    # ── Loading screen ─────────────────────────────────────────────────────
    def _show_loading(self, code):
        """Limpa o resultado atual e exibe a tela de loading animada."""
        self._clear()
        self._loading_screen = LoadingScreen(self.result_frame, code)
        self._loading_screen.pack(fill="both", expand=True)
        # Força altura mínima para a tela de loading ocupar o espaço visível
        self.result_frame.config(height=500)

    def _hide_loading(self):
        """Para a animação e remove a tela de loading."""
        if self._loading_screen:
            self._loading_screen.stop()
            self._loading_screen = None

    # ── Scan ───────────────────────────────────────────────────────────────
    def _on_scan(self, _=None):
        code = self.entry_var.get().strip()
        if not code:
            return
        self.status_lbl.config(text="Consultando banco de dados…", fg=INK3)
        self._set_dot(ORANGE)
        self.btn.config(state="disabled")
        self._show_loading(code)   # ← substitui o resultado atual por loading
        threading.Thread(target=self._fetch, args=(code,), daemon=True).start()

    def _fetch(self, code):
        try:
            rows = buscar_pedido(code)
            self.after(0, lambda: self._render(code, rows))
        except Exception as exc:
            self.after(0, lambda: self._render_error(code, str(exc)))

    # ── Render: resultado ──────────────────────────────────────────────────
    def _render(self, code, rows):
        self._hide_loading()
        self._clear()
        self.btn.config(state="normal")
        self.entry_var.set("")
        self.entry.focus_set()

        if not rows:
            self.status_lbl.config(text=f"Nenhum pedido encontrado: {code}", fg=RED)
            self._set_dot(RED)
            self._render_not_found(code)
            return

        self._scan_count += 1
        atualizar_shipping_status(code)
        salvar_sucesso(code, rows)
        plural = "s" if self._scan_count != 1 else ""
        self.counter_lbl.config(
            text=f"{self._scan_count} scan{plural} realizado{plural}")
        self.status_lbl.config(text=f"Pedido localizado  ·  {code}", fg=GREEN)
        self._set_dot(GREEN)
        self._blink_dot(GREEN)

        row = rows[0]

        # ── Monta lista plana de todos os itens do pacote ──────────────────
        all_items = []

        # Item 1: kit principal
        val = row.get("TShirtSize")
        raw = "" if val is None else str(val).strip()
        tshirt = TSHIRT_SIZES.get(raw, raw) 
        
        all_items.append({
            "kit_name": str(row["KitName"] or ""),
            "img_path": str(row["KitPngPicture"] or ""),
            "event":    str(row["EventName"] or ""),
            "tshirt":   tshirt,          # ← novo
            "quantity": str(row.get("SubscriptionQuantity") or "1"),
            "is_main":  True,
            "tag_text": "KIT PRINCIPAL",
            "tag_fg":   SURFACE,
            "tag_bg":   INK,
        })

        for r in rows:
            bump_name = r["OrderBumpKitName"]
            if bump_name:
                all_items.append({
                    "kit_name": str(bump_name),
                    "img_path": str(r["OrderBumpKitPngPicture"] or ""),
                    "event":    str(r["OrderBumpEventName"] or ""),
                    "tshirt":   tshirt,  # ← mesmo tamanho
                    "quantity": "1",
                    "is_main":  False,
                    "tag_text": "ORDER BUMP",
                    "tag_fg":   ORANGE,
                    "tag_bg":   ORANGE_BG,
                })

        total = len(all_items)
        has_bumps = total > 1
        info = row.get("AdditionalInfo")
        has_info = bool(info and str(info).strip())

        # ── Banners de alerta (topo, antes de tudo) ────────────────────────
        if has_bumps or has_info:
            alert_outer = tk.Frame(self.result_frame, bg=BG, padx=28)
            alert_outer.pack(fill="x", pady=(16, 0))

            if has_bumps:
                bump_names = "  +  ".join(
                    i["event"] or i["kit_name"]
                    for i in all_items if not i["is_main"]
                )
                AlertBanner(
                    alert_outer,
                    icon="📦",
                    title=f"ATENÇÃO — ESTE PEDIDO INCLUI {total - 1} ORDER BUMP{'S' if total - 1 > 1 else ''}",
                    subtitle=f"Incluir no pacote: {bump_names}",
                    bg_color=ALERT_BUMP_BG,
                    fg_color=ALERT_BUMP_FG,
                    border_color=ALERT_BUMP_BORDER,
                ).pack(fill="x", pady=(0, 8))

            if has_info:
                AlertBanner(
                    alert_outer,
                    icon="📋",
                    title="ATENÇÃO — INFORMAÇÕES ADICIONAIS DO CLIENTE",
                    subtitle=str(info).strip(),
                    bg_color=ALERT_INFO_BG,
                    fg_color=ALERT_INFO_FG,
                    border_color=ALERT_INFO_BORDER,
                ).pack(fill="x")

        # ── Header do resultado ────────────────────────────────────────────
        hdr = tk.Frame(self.result_frame, bg=BG, padx=28, pady=20)
        hdr.pack(fill="x")

        lhdr = tk.Frame(hdr, bg=BG)
        lhdr.pack(side="left")
        tk.Label(lhdr, text="CONTEÚDO DO PACOTE", bg=BG, fg=INK3,
                 font=("Helvetica", 8, "bold")).pack(anchor="w")

        code_row = tk.Frame(lhdr, bg=BG)
        code_row.pack(anchor="w", pady=(4, 0))
        tk.Label(code_row, text=code, bg=BG, fg=INK,
                 font=("Courier", 24, "bold")).pack(side="left")
        Tag(code_row, text="✓  ENCONTRADO", fg=GREEN, bg=GREEN_BG).pack(
            side="left", padx=(14, 0), pady=2)

        rhdr = tk.Frame(hdr, bg=BG)
        rhdr.pack(side="right", anchor="e")
        tk.Label(rhdr, text=str(total), bg=BG, fg=INK,
                 font=("Helvetica", 40, "bold")).pack(anchor="e")
        tk.Label(rhdr, text=f"ITEM{'S' if total > 1 else ''}",
                 bg=BG, fg=INK3, font=("Helvetica", 8, "bold")).pack(anchor="e")

        tk.Frame(self.result_frame, bg=BORDER, height=1).pack(fill="x")

        # ── Renderiza cada item como uma linha completa ────────────────────
        for idx, item in enumerate(all_items, start=1):
            if idx > 1:
                sep_row = tk.Frame(self.result_frame, bg=BG, padx=28)
                sep_row.pack(fill="x", pady=(14, 0))
                tk.Frame(sep_row, bg=DIVIDER, height=1).pack(fill="x")

                lbl_row = tk.Frame(self.result_frame, bg=BG, padx=28)
                lbl_row.pack(fill="x", pady=(8, 0))
                tk.Label(lbl_row, text="+ ORDER BUMP", bg=BG, fg=ORANGE,
                         font=("Helvetica", 8, "bold")).pack(side="left")

            KitPanel(
                self.result_frame,
                kit_name=item["kit_name"],
                img_path=item["img_path"],
                tag_text=item["tag_text"],
                tag_fg=item["tag_fg"],
                tag_bg=item["tag_bg"],
                event_name=item["event"],
                is_main=item["is_main"],
                index=f"{idx} DE {total}",
                tshirt=item["tshirt"], 
                quantity=item["quantity"],                
            ).pack(fill="x", padx=28, pady=(12 if idx == 1 else 6, 0))

        # ── Informações adicionais (seção detalhada abaixo dos kits) ──────
        # O alerta acima já avisa; aqui fica o bloco completo para referência.
        if has_info:
            tk.Frame(self.result_frame, bg=BORDER, height=1).pack(
                fill="x", padx=28, pady=(20, 0))
            ai = tk.Frame(self.result_frame, bg=SURFACE,
                          highlightthickness=2, highlightbackground=ALERT_INFO_BORDER,
                          padx=20, pady=16)
            ai.pack(fill="x", padx=28, pady=(12, 0))
            header_row = tk.Frame(ai, bg=SURFACE)
            header_row.pack(fill="x")
            tk.Label(header_row, text="📋", bg=SURFACE, fg=ALERT_INFO_BG,
                     font=("Helvetica", 16)).pack(side="left", padx=(0, 8))
            tk.Label(header_row, text="INFORMAÇÕES ADICIONAIS", bg=SURFACE,
                     fg=ALERT_INFO_BG, font=("Helvetica", 9, "bold")).pack(
                         side="left", anchor="s")
            tk.Label(ai, text=str(info), bg=SURFACE, fg=INK,
                     font=("Helvetica", 12), wraplength=880,
                     justify="left", anchor="w").pack(anchor="w", pady=(10, 0))

        # ── Checklist ─────────────────────────────────────────────────────
        self._render_checklist(all_items)

    def _render_checklist(self, all_items):
        foot = tk.Frame(self.result_frame, bg=BG, padx=28, pady=24)
        foot.pack(fill="x")

        tk.Label(foot, text="CHECKLIST DO PACOTE", bg=BG, fg=INK3,
                 font=("Helvetica", 8, "bold")).pack(anchor="w", pady=(0, 10))

        for idx, item in enumerate(all_items, start=1):
            rf = tk.Frame(foot, bg=BG, pady=5)
            rf.pack(fill="x")

            tk.Label(rf, text="□", bg=BG, fg=BORDER2,
                     font=("Helvetica", 18)).pack(side="left", padx=(0, 10), anchor="n")

            info = tk.Frame(rf, bg=BG)
            info.pack(side="left", fill="x", expand=True)

            top = tk.Frame(info, bg=BG)
            top.pack(anchor="w")
            tk.Label(top, text=f"{idx}.", bg=BG, fg=INK3,
                     font=("Helvetica", 14)).pack(side="left", padx=(0, 6))
            tk.Label(top, text=item.get("event") or item["kit_name"],
                     bg=BG, fg=INK,
                     font=("Helvetica", 14, "bold")).pack(side="left")

            sub = tk.Frame(info, bg=BG)
            sub.pack(anchor="w", pady=(1, 0))
            tk.Label(sub, text=f"     {item['kit_name']}",
                     bg=BG, fg=INK3,
                     font=("Helvetica", 9)).pack(side="left")
            Tag(sub, text=item["tag_text"],
                fg=INK3, bg=SURFACE2).pack(side="left", padx=(8, 0))

        tk.Frame(self.result_frame, bg=BG, height=32).pack()

    # ── Render: não encontrado ─────────────────────────────────────────────
    def _render_not_found(self, code):
        salvar_erro(code, "Código não encontrado")
        box = tk.Frame(self.result_frame, bg=SURFACE,
                       highlightthickness=1, highlightbackground=RED,
                       padx=36, pady=36)
        box.pack(padx=28, pady=40)
        tk.Label(box, text="CÓDIGO NÃO ENCONTRADO", bg=SURFACE, fg=RED,
                 font=("Helvetica", 14, "bold")).pack()
        tk.Label(box, text=code, bg=SURFACE, fg=INK3,
                 font=("Courier", 16)).pack(pady=(6, 0))
        tk.Label(box,
                 text="Verifique se o código está correto ou se o pagamento foi confirmado.",
                 bg=SURFACE, fg=INK3, font=("Helvetica", 9)).pack(pady=(14, 0))

    # ── Render: erro ───────────────────────────────────────────────────────
    def _render_error(self, code, msg):     # ← adicione o parâmetro code
        salvar_erro(code, msg)
        self._hide_loading()
        self._clear()
        self.btn.config(state="normal")
        self.status_lbl.config(text="Erro de conexão com o banco de dados", fg=RED)
        self._set_dot(RED)
        box = tk.Frame(self.result_frame, bg=RED_BG,
                       highlightthickness=1, highlightbackground=RED,
                       padx=28, pady=28)
        box.pack(padx=28, pady=40)
        tk.Label(box, text="ERRO DE CONEXÃO", bg=RED_BG, fg=RED,
                 font=("Helvetica", 12, "bold")).pack(anchor="w")
        tk.Label(box, text=msg, bg=RED_BG, fg=RED,
                 font=("Courier", 9), wraplength=700,
                 justify="left").pack(anchor="w", pady=(8, 0))

    # ── Helpers ────────────────────────────────────────────────────────────
    def _clear(self):
        KitPanel._refs.clear()
        for w in self.result_frame.winfo_children():
            w.destroy()

    def _set_dot(self, color):
        self.dot.config(fg=color)

    def _blink_dot(self, color, times=5):
        if self._blink_job:
            self.after_cancel(self._blink_job)

        def step(n, on):
            if n <= 0:
                self.dot.config(fg=color)
                return
            self.dot.config(fg=color if on else BG)
            self._blink_job = self.after(160, lambda: step(n - 1, not on))

        step(times * 2, False)


if __name__ == "__main__":
    app = ScannerApp()
    app.mainloop()