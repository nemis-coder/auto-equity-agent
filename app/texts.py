"""Textos de la pantalla del asesor a partir de códigos del backend.

Ningún texto decide negocio; solo traduce códigos que calculó el backend.
"""

from __future__ import annotations

STAGES = {
    "VEHICLE_ELIGIBILITY": "Tu auto",
    "PROFILING": "Tu perfil",
    "SIMULATION": "Opciones",
    "DOCUMENT_COLLECTION": "Documentos",
    "DOCUMENT_VALIDATION": "Revisión",
}

STATES = {
    **STAGES,
    "NEEDS_CORRECTION": "Corrección pendiente",
    "HUMAN_REVIEW": "En revisión por un asesor",
    "READY_FOR_FINANCIAL": "Listo para la financiera",
    "REJECTED": "No podemos continuar",
}

REJECTIONS = {
    "VEHICLE_NOT_OWNED": "El auto debe estar a nombre de quien solicita el crédito.",
    "BLOCKING_DEBT": "El auto tiene un adeudo que impide usarlo como garantía.",
}

FIELDS = {
    "owned_by_customer": "El auto está a tu nombre",
    "blocking_debt": "El auto tiene adeudos que impiden usarlo como garantía",
    "has_second_key": "Tienes la segunda llave",
    "vehicle_ref": "Placa o número de serie",
    "make": "Marca",
    "model": "Modelo",
    "year": "Año",
    "declared_owner_name": "Nombre del titular del auto",
    "full_name": "Nombre completo",
    "address": "Domicilio",
    "employment": "Situación laboral",
    "employer_or_activity": "Empleador o actividad",
    "income": "Ingreso",
}

EMPLOYMENT = {"SALARIED": "Asalariado", "SELF_EMPLOYED": "Independiente"}
PERIODS = {
    "MONTHLY": "Mensual",
    "SEMIMONTHLY": "Quincenal (dos por mes)",
    "BIWEEKLY_14D": "Cada 14 días",
    "WEEKLY": "Semanal",
}

ERRORS = {
    "VERSION_CONFLICT": "El caso cambió mientras lo veías. Actualizamos la información.",
    "TERMINAL_CASE": "Este caso ya está cerrado.",
    "RATE_LIMITED": "Demasiadas acciones seguidas. Espera un momento.",
    "INVALID_STATE": "Esa acción no corresponde a la etapa actual del caso.",
    "REVIEW_NOT_OPEN": "Esta revisión ya fue resuelta. Actualizamos la información.",
    "REVIEW_REASON_UNRESOLVED": (
        "El motivo de la revisión sigue vigente. Pide una corrección al cliente, registra una "
        "lectura humana o concilia la operación."
    ),
    "RESOLUTION_NOT_APPLICABLE": "Esa resolución no aplica a la etapa actual del caso.",
    "RECONCILIATION_MISMATCH": "El proveedor no confirma ese resultado; no se aplicó nada.",
    "OPERATION_NOT_UNKNOWN": "La operación ya no está incierta.",
    "PROVIDER_UNAVAILABLE": "No se pudo consultar al proveedor. Intenta de nuevo.",
    "INVALID_TARGETS": "Elige al menos un dato o documento a corregir.",
    "INVALID_FIELDS": "Elige campos del documento y escribe su valor.",
    "EXTRACTION_MISSING": "El documento aún no tiene lectura.",
    "DOCUMENT_NOT_CURRENT": "Ese documento ya fue reemplazado. Actualizamos la información.",
}
REVIEW_REASONS = {
    "CUSTOMER_REQUEST": "El cliente pidió hablar con un asesor",
    "DOCUMENT_CORRECTIONS_EXHAUSTED": "Dos rondas de corrección documental sin éxito",
    "PROVIDER_UNKNOWN": "Resultado incierto de un proveedor externo",
    "TURN_BUDGET_EXCEEDED": "Se agotó el presupuesto de turnos automáticos",
    "TOKEN_BUDGET_EXCEEDED": "Se agotó el presupuesto de tokens del caso",
    "NO_OFFERS_AVAILABLE": "El cotizador de la financiera no dio opciones para el perfil",
    "LEGACY": "Revisión previa a la versión actual",
}
RESOLUTIONS = {
    "REQUEST_CORRECTION": "Corrección solicitada al cliente",
    "AMEND_EXTRACTION": "Lectura humana registrada",
    "RECONCILE_OPERATION": "Operación conciliada",
    "RESUME": "Reanudado",
}
CORRECTION_TARGETS = {
    "IDENTITY": "Identificación oficial",
    "INCOME": "Comprobante de ingresos",
    "VEHICLE_OWNERSHIP": "Documento del auto",
    "profile": "Datos personales e ingreso",
    "vehicle": "Datos del auto",
}
OPERATIONS = {
    "query_credit_bureau": "Consulta de historial",
    "quote_second_key": "Cotización de llave",
    "quote_offers": "Opciones del cotizador",
}

SLOTS = {"IDENTITY": "Identificación oficial", "INCOME": "Comprobante de ingresos",
         "VEHICLE_OWNERSHIP": "Tarjeta de circulación o factura del auto"}  # fmt: skip
DOCUMENT_KINDS = {
    "IDENTITY": "Identificación oficial",
    "PAYSLIP": "Recibo de nómina",
    "INCOME_STATEMENT": "Estado de cuenta de ingresos",
    "VEHICLE_OWNERSHIP": "Tarjeta de circulación o factura",
}
DOCUMENT_STATUS = {"RECEIVED": "Recibido, en lectura", "EXTRACTED": "Leído"}
DOCUMENT_FIELDS = {
    "full_name": "nombre", "employer_name": "empleador", "activity": "actividad",
    "income_amount": "monto del ingreso", "currency": "moneda", "period": "periodicidad",
    "income_basis": "neto o bruto", "period_start": "inicio del periodo",
    "period_end": "fin del periodo", "issue_date": "fecha de emisión",
    "expiry_date": "vigencia", "owner_name": "titular", "vehicle_ref": "placa o serie",
    "address_street": "calle", "address_external_number": "número exterior",
    "address_internal_number": "número interior", "address_neighborhood": "colonia",
    "address_municipality": "municipio", "address_state": "estado",
    "address_postal_code": "código postal",
}  # fmt: skip
CORRECTIONS = {
    "DOCUMENT_MISSING": "Falta cargar {slot}.",
    "EXTRACTION_PENDING": "Aún estamos leyendo {slot}.",
    "DOCUMENT_TYPE_MISMATCH": "El archivo de {slot} no corresponde a ese tipo de documento.",
    "ILLEGIBLE": "No pudimos leer {slot}. Sube una imagen o PDF más nítido.",
    "FIELD_MISSING": "En {slot} no encontramos {field}. Sube un documento donde se vea completo.",
    "EVIDENCE_MISSING": "En {slot} no pudimos ubicar {field}. Sube un documento más claro.",
    "LOW_CONFIDENCE": "En {slot} no pudimos leer con certeza {field}. Sube una imagen más clara.",
    "CONFIDENCE_MISSING": "En {slot} no pudimos confirmar {field}. Sube una imagen más clara.",
    "INCOME_NOT_NET": "El comprobante debe mostrar tu ingreso neto (después de impuestos).",
    "INCOME_BASIS_UNKNOWN": "No pudimos saber si el ingreso del comprobante es neto o bruto.",
    "INCOME_BASIS_UNSUPPORTED_EVIDENCE": (
        "No pudimos respaldar que el importe sea neto con una etiqueta clara del comprobante. "
        "Necesitamos un comprobante que lo indique o que un asesor revise el original."
    ),
    "INCOME_CURRENCY_MISMATCH": "El comprobante debe estar en pesos mexicanos (MXN).",
    "INCOME_CURRENCY_UNKNOWN": "No pudimos identificar la moneda del comprobante.",
    "INCOME_PERIOD_UNKNOWN": "No pudimos identificar la periodicidad del comprobante.",
    "INCOME_PERIOD_UNSUPPORTED_EVIDENCE": (
        "Necesitamos que el comprobante indique la frecuencia de pago; las fechas por sí solas "
        "no la confirman. Puedes aportar otro comprobante o pedir revisión a un asesor."
    ),
    "INCOME_NOT_COMPARABLE": "No pudimos comparar tu ingreso con el comprobante.",
    "INCOME_AMOUNT_UNKNOWN": "No pudimos leer el monto del comprobante.",
    "INCOME_MISMATCH": ("El ingreso del comprobante no coincide con el declarado (tolerancia "
                        "del 10 %). Revisa el monto y la periodicidad que declaraste, o carga "
                        "un comprobante que los sustente."),
    "NAME_MISMATCH": "El nombre de tu identificación no coincide con el que declaraste.",
    "NAME_UNKNOWN": "No pudimos leer el nombre de tu identificación.",
    "ADDRESS_MISMATCH": "El domicilio de tu identificación no coincide con el declarado ({field}).",
    "ADDRESS_FIELD_UNKNOWN": "No pudimos leer el domicilio de tu identificación ({field}).",
    "INCOME_DOCUMENT_TYPE_MISMATCH": ("Para tu situación laboral necesitamos un recibo de nómina "
                                      "(asalariado) o un estado de cuenta de ingresos "
                                      "(independiente)."),
    "INCOME_DOCUMENT_NAME_MISMATCH": "El nombre del comprobante no coincide con el tuyo.",
    "EMPLOYER_MISMATCH": "El empleador o actividad del comprobante no coincide con lo declarado.",
    "IDENTITY_EXPIRED": "Tu identificación está vencida. Carga una vigente.",
    "INCOME_DOCUMENT_TOO_OLD": "El comprobante de ingresos debe ser de los últimos 90 días.",
    "DATE_IN_FUTURE": "Una fecha de {slot} es posterior a hoy.",
    "DATE_MISSING": "No pudimos leer una fecha necesaria de {slot}.",
    "PERIOD_INVALID": "El periodo del comprobante de ingresos no es válido.",
    "VEHICLE_REF_MISMATCH": "La placa o serie del documento no coincide con la del auto declarado.",
    "VEHICLE_OWNER_MISMATCH": "El titular del documento del auto no coincide con tu nombre.",
    "VEHICLE_DATA_UNKNOWN": "No pudimos leer los datos del documento del auto.",
}  # fmt: skip
