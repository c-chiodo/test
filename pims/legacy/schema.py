"""What the legacy ProductionData database looks like, and where each piece lands.

Every physical name here was recovered from the legacy build itself, not
guessed: the table names and join keys from the SQL compiled into
``PIMS.exe`` (the Custom Query builder's FROM/JOIN clauses), the columns from
the entity classes in ``PIMS.xml``. The two disagree in places — ``PIMS.xml``
dates from 2018 and the executable was rebuilt in 2026 with columns the old
entity model does not have (``from_qc_id``, FFA, TFA, BOL numbers on
transactions) — which is exactly why nothing here is trusted until
``python -m pims legacy check`` has compared it with the live database.

Each column is one of:

* **required** — the mirror cannot run without it (keys, quantities, dates);
* **optional** — read when present, left empty when not;
* **candidates** — several plausible names, of which the first that exists wins
  (used where the evidence names the concept but not the spelling).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Col:
    local: str
    candidates: tuple[str, ...]
    required: bool = True

    @property
    def label(self) -> str:
        return " | ".join(self.candidates)


def req(local: str, *legacy: str) -> Col:
    return Col(local, legacy or (local,), True)


def opt(local: str, *legacy: str) -> Col:
    return Col(local, legacy or (local,), False)


@dataclass(frozen=True)
class Table:
    key: str
    physical: tuple[str, ...]           # one or more tables read as UNION ALL
    local: str
    columns: tuple[Col, ...]
    #: The legacy key column, for incremental reads and reconciliation.
    id_column: str = ""
    #: Tables that can be absent without stopping the mirror.
    optional: bool = False
    note: str = ""

    @property
    def label(self) -> str:
        return " + ".join(self.physical)


@dataclass
class LegacyMap:
    tables: list[Table] = field(default_factory=list)

    def get(self, key: str) -> Table:
        return next(t for t in self.tables if t.key == key)

    def with_physical(self, **overrides: tuple[str, ...] | str) -> "LegacyMap":
        """A copy with some physical names replaced — for a site whose tables
        differ from the build this map was recovered from, or for tests."""

        from dataclasses import replace

        tables = []
        for table in self.tables:
            if table.key in overrides:
                value = overrides[table.key]
                table = replace(table, physical=(value,) if isinstance(value, str) else value)
            tables.append(table)
        return LegacyMap(tables)


# ------------------------------------------------------------------ the map
#
# Order matters: reference data first, so the mirror can resolve foreign keys
# as it goes, and the ledger after everything it points at.

LEGACY_MAP = LegacyMap(
    [
        Table("plant", ("dbo.[Plant]",), "plant", (
            req("plant_id", "Plant_id"),
            req("code", "Code"),
            req("name", "Name"),
            opt("active", "Active"),
        ), id_column="Plant_id"),
        Table("company", ("dbo.[Company]",), "company", (
            req("company_id", "Company_id"),
            req("name", "Name"),
            opt("active", "Active"),
        ), id_column="Company_id"),
        Table("department", ("dbo.[Department]",), "department", (
            req("department_id", "Department_id"),
            req("code", "Code"),
            opt("description", "Description"),
            opt("active", "Active"),
        ), id_column="Department_id"),
        Table("order_type", ("dbo.[OrderType]",), "order_type", (
            req("order_type_id", "Ordertype_id"),
            req("code", "Type_code", "Code"),
            opt("description", "Description"),
        ), id_column="Ordertype_id"),
        Table("status", ("dbo.[Status]",), "status", (
            req("status_id", "Status_id"),
            req("name", "Name"),
            opt("description", "Description"),
        ), id_column="Status_id",
            note="Legacy statuses carry no 'terminal' flag; it is inferred from the name."),
        Table("material_type", ("dbo.[MaterialType]",), "material_type", (
            req("material_type_id", "Materialtype_id"),
            req("name", "Description", "Code"),
            opt("department_id", "Department_Id"),
        ), id_column="Materialtype_id"),
        Table("material", ("dbo.[Material]",), "material", (
            req("material_id", "Material_id"),
            req("number", "Number"),
            req("description", "Description"),
            opt("material_type_id", "Materialtype_id"),
            opt("density", "Density"),
            opt("active", "Active"),
        ), id_column="Material_id"),
        Table("location_type", ("dbo.[LocationType]",), "location_type", (
            req("location_type_id", "Locationtype_id"),
            req("name", "Name", "Description"),
        ), id_column="Locationtype_id"),
        Table("location", ("dbo.[Location]",), "location", (
            req("location_id", "Location_id"),
            req("plant_id", "Plant_id"),
            req("number", "Number"),
            opt("description", "Description"),
            opt("location_type_id", "Locationtype_id"),
            opt("company_id", "Company_id"),
            opt("max_capacity", "Max_capacity"),
            opt("bol_required", "Bol_Required"),
            opt("active", "Active"),
        ), id_column="Location_id"),
        Table("customer", ("dbo.[Customer]",), "customer", (
            req("customer_id", "Customer_id"),
            req("gp_custnmbr", "Gp_custnmbr"),
            req("name", "Gp_custname", "Gp_shrtname"),
            opt("city", "Gp_city"),
            opt("state", "Gp_state"),
            opt("active", "Active"),
        ), id_column="Customer_id"),
        Table("vendor", ("dbo.[Vendor]",), "vendor", (
            req("vendor_id", "Vendor_id"),
            req("gp_vendorid", "Gp_vendorid"),
            req("name", "Gp_vendorname"),
            opt("city", "Gp_city"),
            opt("state", "Gp_state"),
            opt("active", "Active"),
        ), id_column="Vendor_id"),
        Table("transaction_type", ("dbo.[TransType]",), "transaction_type", (
            req("transaction_type_id", "TransType_Id", "Transtype_id"),
            req("name", "Name", "Description", "TransType_Name", "Transtype"),
        ), id_column="TransType_Id",
            note="Legacy types are classified into RECEIVE/PRODUCE/MOVE/LOAD/SHIP/SHRINK/"
                 "ADJUST by name; anything unclassifiable is reported, and still counts "
                 "toward balances."),
        # Users live in a different database on the same server. If the login
        # cannot read it, transactions still mirror; they are attributed to a
        # placeholder per legacy user id.
        Table("user", ("FECoreData.dbo.[User]",), "app_user", (
            req("user_id", "user_id", "User_id"),
            opt("username", "User_name", "UserName", "Username", "Login", "Login_name"),
            opt("full_name", "Full_name", "FullName", "Name", "Display_name"),
            opt("email", "Email", "Email_address"),
            opt("active", "Active"),
        ), id_column="user_id", optional=True),
        Table("user_plant_access", ("dbo.[UserPlantAccess]",), "user_plant_access", (
            req("user_id", "User_id"),
            req("plant_id", "Plant_id"),
        ), optional=True),
        Table("order", ("dbo.[Order]",), "order", (
            req("order_id", "Order_id"),
            req("order_type_id", "Ordertype_id"),
            req("plant_id", "Plant_id"),
            req("status_id", "Status_id"),
            opt("order_date", "Order_date"),
            opt("due_date", "Due_date"),
            opt("order_reference", "Order_reference"),
            opt("company_id", "Company_id"),
            opt("department_id", "Department_id"),
            opt("blend_serial_number", "Blend_serial_number"),
            opt("vendor_id", "Vendor_id"),
            opt("customer_id", "Customer_id"),
            opt("material_one_id", "Material_one_id"),
            opt("material_two_id", "Material_two_id"),
            opt("material_three_id", "Material_three_id"),
            opt("material_four_id", "Material_four_id"),
            opt("material_one_quantity", "Material_one_quantity"),
            opt("ship_method", "Ship_method"),
            opt("trailer_number", "Trailer_number"),
            opt("comments", "Comments"),
            opt("active", "Active"),
            opt("date_added", "Date_added"),
            opt("added_by", "Added_by"),
            opt("date_modified", "Date_modified"),
            opt("modified_by", "Modified_by"),
        ), id_column="Order_id"),
        # The legacy app archives old transactions into a second table of the
        # same shape and reads both with UNION ALL; so does the mirror.
        Table("transaction", ("dbo.[transaction]", "dbo.[Transaction_Archive]"),
              "inventory_transaction", (
            req("transaction_id", "Transaction_id"),
            req("transaction_type_id", "Transtype_id", "TransType_Id"),
            req("plant_id", "Plant_id"),
            req("transaction_date", "Transaction_date"),
            opt("parent_transaction_id", "Parent_transaction_id"),
            opt("user_id", "User_id"),
            opt("department_id", "Department_id"),
            opt("user_date", "User_date"),
            opt("order_id", "Order_id"),
            opt("from_material_id", "From_material_id"),
            opt("from_location_id", "From_location_id"),
            opt("from_qty", "From_qty"),
            opt("to_material_id", "To_material_id"),
            opt("to_location_id", "To_location_id"),
            opt("to_qty", "To_qty"),
            opt("from_bol", "From_bol_number", "From_BOL_Number", "From_bol"),
            opt("to_bol", "To_bol_number", "To_BOL_Number", "To_bol"),
            opt("trailer_number", "Trailer_number"),
            opt("employee_hours", "Employee_hours"),
            opt("tank_hours", "From_location_hours"),
            opt("remarks", "Remarks"),
            opt("comments", "Comments"),
        ), id_column="Transaction_id"),
        Table("qc", ("dbo.[QC]",), "qc", (
            req("qc_id", "Qc_id"),
            req("order_id", "Order_id"),
            opt("bol_number", "Bol_number"),
            opt("test_date", "Test_date"),
            opt("performed_by", "Performed_by"),
            opt("moisture", "Moisture"),
            opt("temp", "Temp"),
            opt("ph", "Ph"),
            opt("ffa", "FFA", "Ffa"),
            opt("tfa", "TFA", "Tfa"),
            opt("spintest_fallout", "Spintest_fallout"),
            opt("flash_pf", "Flash_pf", "Flash"),
            opt("steam_on", "Steam_On", "Steam_on"),
            opt("seal_number", "Seal_number"),
            opt("last_material_hauled", "Last_material_hauled"),
            opt("sample_number", "Sample_number"),
            opt("blend_serial_number", "Blend_serial_number"),
            opt("comments", "Comments"),
            opt("active", "Active"),
            opt("date_added", "Date_added"),
            opt("added_by", "Added_by"),
        ), id_column="Qc_id"),
        Table("pending_shipment", ("dbo.[PendingShipments]",), "pending_shipment", (
            req("stage_id", "Stage_id"),
            req("order_id", "Order_id"),
            req("transaction_id", "Transaction_id"),
            opt("trailer_number", "Trailer_number"),
            opt("quantity", "Quantity"),
            opt("shipped", "Delete_flag"),
        ), id_column="Stage_id",
            note="Delete_flag is read as 'no longer staged'."),
        Table("qa_question", ("dbo.[QAQuestion]",), "qa_question", (
            req("question_id", "Question_id"),
            req("question", "Question"),
            opt("enabled", "Enabled"),
        ), id_column="Question_id", optional=True),
        Table("qa_header", ("dbo.[QAHeader]",), "qa_header", (
            req("header_id", "Header_id"),
            req("order_id", "Order_id"),
            req("plant_id", "Plant_id"),
            opt("qc_id", "Qc_id"),
            opt("trailer_number", "Trailer_number"),
            opt("trailer_load_time", "Trailer_load_time"),
            opt("comments", "Comments"),
            opt("voided", "Voided"),
            opt("date_added", "Date_added"),
            opt("added_by", "Added_by"),
        ), id_column="Header_id", optional=True),
        Table("qa_response", ("dbo.[QAResponse]",), "qa_response", (
            req("response_id", "Response_id"),
            req("header_id", "Header_id"),
            req("question_id", "Question_id"),
            opt("response", "Response"),
        ), id_column="Response_id", optional=True),
    ]
)
