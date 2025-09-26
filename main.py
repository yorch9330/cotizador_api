# main.py
import io
import os
from datetime import date
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import StreamingResponse

# NOTA: Imports de ReportLab **no** están aquí al nivel superior.
# Se importan dentro de `generar_pdf_bytes` (lazy import) para evitar
# que la app falle en tiempo de arranque si reportlab no está instalado.

# ---------------------------
# Models (Pydantic)
# ---------------------------
class SonidoDirectoIn(BaseModel):
    dias: int = 0
    shotgun: int = 0
    lavalier: int = 0
    monitoreo: int = 0
    timecode: int = 0
    grabadora: Optional[str] = ""   # "6", "10" o ""
    sonidista: bool = False
    microfonista: bool = False

class PostProduccionIn(BaseModel):
    minutos: int = 0
    mezcla: Optional[str] = "stereo"  # "stereo" o "5.1"

class CotizarIn(BaseModel):
    cliente: Optional[str] = "Cliente sin nombre"
    servicio: str   # "sonido_directo", "postproduccion", "ambos"
    sonido: Optional[SonidoDirectoIn] = None
    post: Optional[PostProduccionIn] = None
    aplicar_iva: bool = False
    descuento_pct: Optional[int] = 0  # 0,10,20,30
    fecha: Optional[str] = None   # si no se envía, la API pone la fecha de hoy
    pdf_filename: Optional[str] = None

class ItemOut(BaseModel):
    descripcion: str
    cantidad: int
    duracion: str
    unitario: int
    subtotal: int

class CotizarOut(BaseModel):
    cliente: str
    fecha: str
    items: List[ItemOut]
    subtotal_sd: int
    subtotal_post: int
    iva: int
    descuento: int
    total: int

# ---------------------------
# App
# ---------------------------
app = FastAPI(title="API - Cotizador 48 Voltios")

# Allow CORS from anywhere (ajusta en producción)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# Precios (constantes)
# ---------------------------
P_SHOTGUN = 150_000
P_LAVALIER = 80_000
P_MONITOREO = 60_000
P_TIMECODE = 40_000
P_MIXPRE6 = 150_000
P_MIXPRE10 = 200_000
P_SONIDISTA = 400_000
P_MICROFONISTA = 250_000
P_POST_STEREO = 300_000
P_POST_5_1 = 400_000

# Ruta base para buscar logo/firma: mismo directorio que este archivo
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------
# Lógica de cálculo
# ---------------------------
def calcular_items(c: CotizarIn):
    items = []
    subtotal_sd = 0
    subtotal_post = 0

    servicio = (c.servicio or "").lower()

    # SONIDO DIRECTO
    if servicio in ("sonido_directo", "ambos"):
        sd = c.sonido or SonidoDirectoIn()
        dias = max(0, int(sd.dias or 0))

        if dias > 0:
            if sd.shotgun and sd.shotgun > 0:
                sub = sd.shotgun * P_SHOTGUN * dias
                items.append({"descripcion": "Micrófono Shotgun", "cantidad": sd.shotgun,
                              "duracion": f"{dias} días", "unitario": P_SHOTGUN, "subtotal": sub})
                subtotal_sd += sub

            if sd.lavalier and sd.lavalier > 0:
                sub = sd.lavalier * P_LAVALIER * dias
                items.append({"descripcion": "Sistemas inalámbricos Lavalier", "cantidad": sd.lavalier,
                              "duracion": f"{dias} días", "unitario": P_LAVALIER, "subtotal": sub})
                subtotal_sd += sub

            if sd.monitoreo and sd.monitoreo > 0:
                sub = sd.monitoreo * P_MONITOREO * dias
                items.append({"descripcion": "Sistemas de Monitoreo", "cantidad": sd.monitoreo,
                              "duracion": f"{dias} días", "unitario": P_MONITOREO, "subtotal": sub})
                subtotal_sd += sub

            if sd.timecode and sd.timecode > 0:
                sub = sd.timecode * P_TIMECODE * dias
                items.append({"descripcion": "Sistemas Time Code", "cantidad": sd.timecode,
                              "duracion": f"{dias} días", "unitario": P_TIMECODE, "subtotal": sub})
                subtotal_sd += sub

            if sd.grabadora == "6":
                sub = P_MIXPRE6 * dias
                items.append({"descripcion": "Grabadora MixPre-6", "cantidad": 1,
                              "duracion": f"{dias} días", "unitario": P_MIXPRE6, "subtotal": sub})
                subtotal_sd += sub
            elif sd.grabadora == "10":
                sub = P_MIXPRE10 * dias
                items.append({"descripcion": "Grabadora MixPre-10", "cantidad": 1,
                              "duracion": f"{dias} días", "unitario": P_MIXPRE10, "subtotal": sub})
                subtotal_sd += sub

        profesional_days = dias if dias > 0 else 1
        if sd.sonidista:
            sub = P_SONIDISTA * profesional_days
            items.append({"descripcion": "Sonidista", "cantidad": 1,
                          "duracion": f"{profesional_days} días", "unitario": P_SONIDISTA, "subtotal": sub})
            subtotal_sd += sub
        if sd.microfonista:
            sub = P_MICROFONISTA * profesional_days
            items.append({"descripcion": "Microfonista", "cantidad": 1,
                          "duracion": f"{profesional_days} días", "unitario": P_MICROFONISTA, "subtotal": sub})
            subtotal_sd += sub

    # POSTPRODUCCIÓN
    if servicio in ("postproduccion", "ambos"):
        post = c.post or PostProduccionIn()
        minutos = max(0, int(post.minutos or 0))
        if minutos > 0:
            mezcla = (post.mezcla or "stereo").lower()
            if mezcla == "stereo":
                rate = P_POST_STEREO
                descripcion = "Postproducción Estéreo"
            else:
                rate = P_POST_5_1
                descripcion = "Postproducción 5.1"
            sub = minutos * rate
            items.append({"descripcion": descripcion, "cantidad": 1,
                          "duracion": f"{minutos} min", "unitario": rate, "subtotal": sub})
            subtotal_post += sub

    return items, subtotal_sd, subtotal_post


# ---------------------------
# Cálculo final y response
# ---------------------------
def calcular_respuesta(c: CotizarIn):
    items, subtotal_sd, subtotal_post = calcular_items(c)
    total_pre = subtotal_sd + subtotal_post

    iva_amt = int(round(total_pre * 0.19)) if c.aplicar_iva else 0
    total_con_iva = total_pre + iva_amt

    descuento_pct = int(c.descuento_pct or 0)
    descuento_amt = int(round(total_con_iva * (descuento_pct / 100.0))) if descuento_pct else 0
    total_final = total_con_iva - descuento_amt

    fecha_str = c.fecha if c.fecha else date.today().strftime("%d/%m/%Y")
    cliente = c.cliente or "Cliente sin nombre"

    # Convertir items a salida (tipos y estructura)
    items_out = []
    for it in items:
        items_out.append(ItemOut(
            descripcion=it["descripcion"],
            cantidad=int(it["cantidad"]),
            duracion=str(it["duracion"]),
            unitario=int(it["unitario"]),
            subtotal=int(it["subtotal"])
        ))

    resp = CotizarOut(
        cliente=cliente,
        fecha=fecha_str,
        items=items_out,
        subtotal_sd=int(subtotal_sd),
        subtotal_post=int(subtotal_post),
        iva=int(iva_amt),
        descuento=int(descuento_amt),
        total=int(total_final)
    )
    return resp


# ---------------------------
# PDF generator (lazy import de reportlab)
# ---------------------------
def generar_pdf_bytes(filename: str, items: List[dict], subtotal_sd: int, subtotal_post: int,
                      total: int, cliente_info: dict, iva_amt=0, descuento_amt=0,
                      iva_aplicado=False, descuento_pct=0) -> bytes:
    # IMPORTS dentro de la función (lazy import)
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                        Paragraph, Spacer, Image as RLImage)
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.utils import ImageReader
    except Exception as e:
        # Si reportlab no está disponible, devolvemos un error claro.
        raise RuntimeError("ReportLab no está instalado en este entorno. Añade 'reportlab' y 'pillow' al requirements.txt") from e

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elementos = []
    estilos = getSampleStyleSheet()

    cliente_nombre = cliente_info.get("cliente", "")
    fecha_str = cliente_info.get("fecha", "")

    info_html = (
        f"<b>48 VOLTIOS S.A.S</b><br/>"
        "<b>NIT. 901641620-5</b><br/>"
        "Sonido Directo y Postproducción de Sonido<br/>"
        "Contacto: 48voltios.info@gmail.com | Tel: +57 318 377 2397<br/><br/>"
        f"<b>Cliente:</b> {cliente_nombre}<br/>"
        f"<b>Fecha:</b> {fecha_str}"
    )
    estilo_info = estilos['Normal'].clone('info')
    estilo_info.alignment = TA_RIGHT

    # Intentar cargar logo y firma; si no están, cae en blanco
    logo_path = os.path.join(BASE_DIR, "logo48v.png")
    firma_path = os.path.join(BASE_DIR, "firma_jorge.png")

    try:
        if os.path.exists(logo_path):
            logo = RLImage(ImageReader(logo_path), width=120, height=60)
        else:
            raise FileNotFoundError("logo no encontrado")
    except Exception:
        from reportlab.platypus import Paragraph  # reimport en caso de entrar aquí
        logo = Paragraph("", estilos['Normal'])

    header_table = Table([[logo, Paragraph(info_html, estilo_info)]], colWidths=[130, 360])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elementos.append(header_table)
    elementos.append(Spacer(1, 12))
    elementos.append(Paragraph("COTIZACIÓN DETALLADA", estilos['Heading2']))
    elementos.append(Spacer(1, 12))

    # Tabla principal
    datos = [["Descripción", "Cantidad", "Días/Min", "Valor Unitario", "Subtotal"]]
    for it in items:
        datos.append([
            it["descripcion"],
            str(it["cantidad"]),
            str(it["duracion"]),
            f"${it['unitario']:,}",
            f"${it['subtotal']:,}"
        ])

    datos.append(["", "", "", "Subtotal Sonido Directo", f"${subtotal_sd:,}"])
    datos.append(["", "", "", "Subtotal Postproducción", f"${subtotal_post:,}"])
    if iva_aplicado and iva_amt:
        datos.append(["", "", "", "IVA (19%)", f"${iva_amt:,}"])
    if descuento_pct and descuento_amt:
        datos.append(["", "", "", f"Descuento ({descuento_pct}%)", f"-${descuento_amt:,}"])
    datos.append(["", "", "", "TOTAL", f"${total:,}"])

    tabla = Table(datos, colWidths=[200, 60, 70, 100, 100])
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#4AD395")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke)
    ]))
    elementos.append(tabla)

    # Firma
    elementos.append(Spacer(1, 24))
    elementos.append(Paragraph("Cordialmente,", estilos['Normal']))
    elementos.append(Spacer(1, 12))
    try:
        if os.path.exists(firma_path):
            firma_img = RLImage(ImageReader(firma_path), width=140, height=80, hAlign='LEFT')
            elementos.append(firma_img)
        else:
            raise FileNotFoundError("firma no encontrada")
    except Exception:
        elementos.append(Paragraph("(firma no disponible)", estilos['Normal']))

    elementos.append(Spacer(1, 6))
    elementos.append(Paragraph("<b>JORGE BAHAMÓN</b>", estilos['Normal']))
    elementos.append(Paragraph("Productor de Audio", estilos['Normal']))
    elementos.append(Paragraph("Contacto: +57 318 377 2397", estilos['Normal']))
    elementos.append(Spacer(1, 12))

    doc.build(elementos)
    buffer.seek(0)
    return buffer.read()


# ---------------------------
# Endpoints
# ---------------------------

@app.get("/")
def root():
    return {"message": "API Cotizador 48 Voltios - OK"}


@app.post("/cotizar/json", response_model=CotizarOut)
def cotizar_json(payload: CotizarIn):
    try:
        resp = calcular_respuesta(payload)
        return resp
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/cotizar/pdf")
def cotizar_pdf(payload: CotizarIn):
    """
    Devuelve application/pdf con la cotización.
    En el JSON de entrada puedes usar "pdf_filename" para sugerir un nombre, p.e. "cotizacion_cliente.pdf".
    """
    try:
        # calcular
        resp = calcular_respuesta(payload)
        # reconstruir items como dicts
        items = [{"descripcion": i.descripcion, "cantidad": i.cantidad,
                  "duracion": i.duracion, "unitario": i.unitario, "subtotal": i.subtotal}
                 for i in resp.items]

        iva_aplicado = bool(payload.aplicar_iva)
        iva_amt = int(resp.iva)
        descuento_amt = int(resp.descuento)
        descuento_pct = int(payload.descuento_pct or 0)

        cliente_info = {"cliente": resp.cliente, "fecha": resp.fecha}
        # Sanitizar filename simple
        safe_cliente = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in resp.cliente)
        filename = payload.pdf_filename or f"cotizacion_{safe_cliente}.pdf"

        # generar (aquí puede lanzar RuntimeError si falta reportlab)
        try:
            pdf_bytes = generar_pdf_bytes(filename, items, resp.subtotal_sd, resp.subtotal_post,
                                          resp.total, cliente_info,
                                          iva_amt=iva_amt, descuento_amt=descuento_amt,
                                          iva_aplicado=iva_aplicado, descuento_pct=descuento_pct)
        except RuntimeError as e:
            # Error claro si reportlab no está instalado
            raise HTTPException(status_code=500, detail=str(e))

        return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf",
                                 headers={"Content-Disposition": f"attachment; filename={filename}"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}
