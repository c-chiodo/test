-- =====================================================================
-- PIMS companion — READINESS CHECK  (READ-ONLY)
-- Run in VS Code (MSSQL) or SSMS against FESQLPROD01\PRODUCTION, database
-- ProductionData — or, better, against the restored copy / readable
-- secondary the companion will actually read.
--
-- Every statement below is a SELECT over catalog views and metadata.
-- No EXEC, no dynamic SQL, no temp tables, no table scans: row counts come
-- from sys.partitions. Nothing here writes to anything.
--
-- Generated from pims/legacy/schema.py — regenerate with
--     python -m pims legacy readiness-sql
-- =====================================================================
SET NOCOUNT ON;

PRINT '========== 1. Is this login read-only? (the companion refuses to run otherwise) ==========';
SELECT
    login_name        = SUSER_SNAME(),
    database_name     = DB_NAME(),
    is_sysadmin       = IS_SRVROLEMEMBER('sysadmin'),
    is_db_owner       = IS_MEMBER('db_owner'),
    is_datawriter     = IS_MEMBER('db_datawriter'),
    is_ddladmin       = IS_MEMBER('db_ddladmin'),
    can_select        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'SELECT'),
    can_insert        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT'),
    can_update        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE'),
    can_delete        = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE'),
    can_execute       = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE'),
    can_alter         = HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER'),
    verdict = CASE
        WHEN IS_SRVROLEMEMBER('sysadmin') = 1 OR IS_MEMBER('db_owner') = 1
          OR IS_MEMBER('db_datawriter') = 1 OR IS_MEMBER('db_ddladmin') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE') = 1
          OR HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER') = 1
        THEN 'NOT READ-ONLY - the companion will refuse this login; ask for db_datareader only'
        ELSE 'read-only - OK'
    END;

PRINT '========== 2. Table-level grants that bypass the database-level answer ==========';
SELECT t.name AS table_name, p.permission_name, p.state_desc, pr.name AS grantee
FROM sys.database_permissions p
JOIN sys.tables t              ON t.object_id = p.major_id
JOIN sys.database_principals pr ON pr.principal_id = p.grantee_principal_id
WHERE p.class = 1
  AND p.permission_name IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CONTROL')
  AND p.state IN ('G', 'W')
  AND t.name IN ('Company', 'Customer', 'Department', 'Location', 'LocationType', 'Material', 'MaterialType', 'Order', 'OrderType', 'PendingShipments', 'Plant', 'QAHeader', 'QAQuestion', 'QAResponse', 'QC', 'Status', 'TransType', 'Transaction_Archive', 'UserPlantAccess', 'Vendor', 'transaction')
  AND (pr.name = USER_NAME() OR IS_MEMBER(pr.name) = 1)
ORDER BY t.name, p.permission_name;
-- Expect no rows.

PRINT '========== 3. The tables the companion reads, and their size (metadata only) ==========';
SELECT t.name AS table_name, SUM(ps.row_count) AS approx_rows,
       CAST(SUM(ps.used_page_count) * 8 / 1024.0 AS DECIMAL(12, 1)) AS used_mb
FROM sys.tables t
JOIN sys.dm_db_partition_stats ps ON ps.object_id = t.object_id AND ps.index_id IN (0, 1)
WHERE t.name IN ('Company', 'Customer', 'Department', 'Location', 'LocationType', 'Material', 'MaterialType', 'Order', 'OrderType', 'PendingShipments', 'Plant', 'QAHeader', 'QAQuestion', 'QAResponse', 'QC', 'Status', 'TransType', 'Transaction_Archive', 'UserPlantAccess', 'Vendor', 'transaction')
GROUP BY t.name
ORDER BY t.name;
-- If dm_db_partition_stats is refused (it needs VIEW DATABASE STATE), this
-- fallback reads the same numbers from sys.partitions:
SELECT t.name AS table_name, SUM(p.rows) AS approx_rows
FROM sys.tables t
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
WHERE t.name IN ('Company', 'Customer', 'Department', 'Location', 'LocationType', 'Material', 'MaterialType', 'Order', 'OrderType', 'PendingShipments', 'Plant', 'QAHeader', 'QAQuestion', 'QAResponse', 'QC', 'Status', 'TransType', 'Transaction_Archive', 'UserPlantAccess', 'Vendor', 'transaction')
GROUP BY t.name
ORDER BY t.name;

PRINT '========== 4. Columns the map expects: FOUND / MISSING ==========';
WITH expected (table_name, column_name, is_required, maps_to) AS (
    SELECT * FROM (VALUES
    ('Plant', 'Plant_id', 1, 'plant_id'),
    ('Plant', 'Code', 1, 'code'),
    ('Plant', 'Name', 1, 'name'),
    ('Plant', 'Active', 0, 'active'),
    ('Company', 'Company_id', 1, 'company_id'),
    ('Company', 'Name', 1, 'name'),
    ('Company', 'Active', 0, 'active'),
    ('Department', 'Department_id', 1, 'department_id'),
    ('Department', 'Code', 1, 'code'),
    ('Department', 'Description', 0, 'description'),
    ('Department', 'Active', 0, 'active'),
    ('OrderType', 'Ordertype_id', 1, 'order_type_id'),
    ('OrderType', 'Type_code', 1, 'code'),
    ('OrderType', 'Code', 1, 'code'),
    ('OrderType', 'Description', 0, 'description'),
    ('Status', 'Status_id', 1, 'status_id'),
    ('Status', 'Name', 1, 'name'),
    ('Status', 'Description', 0, 'description'),
    ('MaterialType', 'Materialtype_id', 1, 'material_type_id'),
    ('MaterialType', 'Description', 1, 'name'),
    ('MaterialType', 'Code', 1, 'name'),
    ('MaterialType', 'Department_Id', 0, 'department_id'),
    ('Material', 'Material_id', 1, 'material_id'),
    ('Material', 'Number', 1, 'number'),
    ('Material', 'Description', 1, 'description'),
    ('Material', 'Materialtype_id', 0, 'material_type_id'),
    ('Material', 'Density', 0, 'density'),
    ('Material', 'Active', 0, 'active'),
    ('LocationType', 'Locationtype_id', 1, 'location_type_id'),
    ('LocationType', 'Name', 1, 'name'),
    ('LocationType', 'Description', 1, 'name'),
    ('Location', 'Location_id', 1, 'location_id'),
    ('Location', 'Plant_id', 1, 'plant_id'),
    ('Location', 'Number', 1, 'number'),
    ('Location', 'Description', 0, 'description'),
    ('Location', 'Locationtype_id', 0, 'location_type_id'),
    ('Location', 'Company_id', 0, 'company_id'),
    ('Location', 'Max_capacity', 0, 'max_capacity'),
    ('Location', 'Bol_Required', 0, 'bol_required'),
    ('Location', 'Active', 0, 'active'),
    ('Customer', 'Customer_id', 1, 'customer_id'),
    ('Customer', 'Gp_custnmbr', 1, 'gp_custnmbr'),
    ('Customer', 'Gp_custname', 1, 'name'),
    ('Customer', 'Gp_shrtname', 1, 'name'),
    ('Customer', 'Gp_city', 0, 'city'),
    ('Customer', 'Gp_state', 0, 'state'),
    ('Customer', 'Active', 0, 'active'),
    ('Vendor', 'Vendor_id', 1, 'vendor_id'),
    ('Vendor', 'Gp_vendorid', 1, 'gp_vendorid'),
    ('Vendor', 'Gp_vendorname', 1, 'name'),
    ('Vendor', 'Gp_city', 0, 'city'),
    ('Vendor', 'Gp_state', 0, 'state'),
    ('Vendor', 'Active', 0, 'active'),
    ('TransType', 'TransType_Id', 1, 'transaction_type_id'),
    ('TransType', 'Transtype_id', 1, 'transaction_type_id'),
    ('TransType', 'Name', 1, 'name'),
    ('TransType', 'Description', 1, 'name'),
    ('TransType', 'TransType_Name', 1, 'name'),
    ('TransType', 'Transtype', 1, 'name'),
    ('UserPlantAccess', 'User_id', 1, 'user_id'),
    ('UserPlantAccess', 'Plant_id', 1, 'plant_id'),
    ('Order', 'Order_id', 1, 'order_id'),
    ('Order', 'Ordertype_id', 1, 'order_type_id'),
    ('Order', 'Plant_id', 1, 'plant_id'),
    ('Order', 'Status_id', 1, 'status_id'),
    ('Order', 'Order_date', 0, 'order_date'),
    ('Order', 'Due_date', 0, 'due_date'),
    ('Order', 'Order_reference', 0, 'order_reference'),
    ('Order', 'Company_id', 0, 'company_id'),
    ('Order', 'Department_id', 0, 'department_id'),
    ('Order', 'Blend_serial_number', 0, 'blend_serial_number'),
    ('Order', 'Vendor_id', 0, 'vendor_id'),
    ('Order', 'Customer_id', 0, 'customer_id'),
    ('Order', 'Material_one_id', 0, 'material_one_id'),
    ('Order', 'Material_two_id', 0, 'material_two_id'),
    ('Order', 'Material_three_id', 0, 'material_three_id'),
    ('Order', 'Material_four_id', 0, 'material_four_id'),
    ('Order', 'Material_one_quantity', 0, 'material_one_quantity'),
    ('Order', 'Ship_method', 0, 'ship_method'),
    ('Order', 'Trailer_number', 0, 'trailer_number'),
    ('Order', 'Comments', 0, 'comments'),
    ('Order', 'Active', 0, 'active'),
    ('Order', 'Date_added', 0, 'date_added'),
    ('Order', 'Added_by', 0, 'added_by'),
    ('Order', 'Date_modified', 0, 'date_modified'),
    ('Order', 'Modified_by', 0, 'modified_by'),
    ('transaction', 'Transaction_id', 1, 'transaction_id'),
    ('transaction', 'Transtype_id', 1, 'transaction_type_id'),
    ('transaction', 'TransType_Id', 1, 'transaction_type_id'),
    ('transaction', 'Plant_id', 1, 'plant_id'),
    ('transaction', 'Transaction_date', 1, 'transaction_date'),
    ('transaction', 'Parent_transaction_id', 0, 'parent_transaction_id'),
    ('transaction', 'User_id', 0, 'user_id'),
    ('transaction', 'Department_id', 0, 'department_id'),
    ('transaction', 'User_date', 0, 'user_date'),
    ('transaction', 'Order_id', 0, 'order_id'),
    ('transaction', 'From_material_id', 0, 'from_material_id'),
    ('transaction', 'From_location_id', 0, 'from_location_id'),
    ('transaction', 'From_qty', 0, 'from_qty'),
    ('transaction', 'To_material_id', 0, 'to_material_id'),
    ('transaction', 'To_location_id', 0, 'to_location_id'),
    ('transaction', 'To_qty', 0, 'to_qty'),
    ('transaction', 'From_bol_number', 0, 'from_bol'),
    ('transaction', 'From_BOL_Number', 0, 'from_bol'),
    ('transaction', 'From_bol', 0, 'from_bol'),
    ('transaction', 'To_bol_number', 0, 'to_bol'),
    ('transaction', 'To_BOL_Number', 0, 'to_bol'),
    ('transaction', 'To_bol', 0, 'to_bol'),
    ('transaction', 'Trailer_number', 0, 'trailer_number'),
    ('transaction', 'Employee_hours', 0, 'employee_hours'),
    ('transaction', 'From_location_hours', 0, 'tank_hours'),
    ('transaction', 'Remarks', 0, 'remarks'),
    ('transaction', 'Comments', 0, 'comments'),
    ('Transaction_Archive', 'Transaction_id', 1, 'transaction_id'),
    ('Transaction_Archive', 'Transtype_id', 1, 'transaction_type_id'),
    ('Transaction_Archive', 'TransType_Id', 1, 'transaction_type_id'),
    ('Transaction_Archive', 'Plant_id', 1, 'plant_id'),
    ('Transaction_Archive', 'Transaction_date', 1, 'transaction_date'),
    ('Transaction_Archive', 'Parent_transaction_id', 0, 'parent_transaction_id'),
    ('Transaction_Archive', 'User_id', 0, 'user_id'),
    ('Transaction_Archive', 'Department_id', 0, 'department_id'),
    ('Transaction_Archive', 'User_date', 0, 'user_date'),
    ('Transaction_Archive', 'Order_id', 0, 'order_id'),
    ('Transaction_Archive', 'From_material_id', 0, 'from_material_id'),
    ('Transaction_Archive', 'From_location_id', 0, 'from_location_id'),
    ('Transaction_Archive', 'From_qty', 0, 'from_qty'),
    ('Transaction_Archive', 'To_material_id', 0, 'to_material_id'),
    ('Transaction_Archive', 'To_location_id', 0, 'to_location_id'),
    ('Transaction_Archive', 'To_qty', 0, 'to_qty'),
    ('Transaction_Archive', 'From_bol_number', 0, 'from_bol'),
    ('Transaction_Archive', 'From_BOL_Number', 0, 'from_bol'),
    ('Transaction_Archive', 'From_bol', 0, 'from_bol'),
    ('Transaction_Archive', 'To_bol_number', 0, 'to_bol'),
    ('Transaction_Archive', 'To_BOL_Number', 0, 'to_bol'),
    ('Transaction_Archive', 'To_bol', 0, 'to_bol'),
    ('Transaction_Archive', 'Trailer_number', 0, 'trailer_number'),
    ('Transaction_Archive', 'Employee_hours', 0, 'employee_hours'),
    ('Transaction_Archive', 'From_location_hours', 0, 'tank_hours'),
    ('Transaction_Archive', 'Remarks', 0, 'remarks'),
    ('Transaction_Archive', 'Comments', 0, 'comments'),
    ('QC', 'Qc_id', 1, 'qc_id'),
    ('QC', 'Order_id', 1, 'order_id'),
    ('QC', 'Bol_number', 0, 'bol_number'),
    ('QC', 'Test_date', 0, 'test_date'),
    ('QC', 'Performed_by', 0, 'performed_by'),
    ('QC', 'Moisture', 0, 'moisture'),
    ('QC', 'Temp', 0, 'temp'),
    ('QC', 'Ph', 0, 'ph'),
    ('QC', 'FFA', 0, 'ffa'),
    ('QC', 'Ffa', 0, 'ffa'),
    ('QC', 'TFA', 0, 'tfa'),
    ('QC', 'Tfa', 0, 'tfa'),
    ('QC', 'Spintest_fallout', 0, 'spintest_fallout'),
    ('QC', 'Flash_pf', 0, 'flash_pf'),
    ('QC', 'Flash', 0, 'flash_pf'),
    ('QC', 'Steam_On', 0, 'steam_on'),
    ('QC', 'Steam_on', 0, 'steam_on'),
    ('QC', 'Seal_number', 0, 'seal_number'),
    ('QC', 'Last_material_hauled', 0, 'last_material_hauled'),
    ('QC', 'Sample_number', 0, 'sample_number'),
    ('QC', 'Blend_serial_number', 0, 'blend_serial_number'),
    ('QC', 'Comments', 0, 'comments'),
    ('QC', 'Active', 0, 'active'),
    ('QC', 'Date_added', 0, 'date_added'),
    ('QC', 'Added_by', 0, 'added_by'),
    ('PendingShipments', 'Stage_id', 1, 'stage_id'),
    ('PendingShipments', 'Order_id', 1, 'order_id'),
    ('PendingShipments', 'Transaction_id', 1, 'transaction_id'),
    ('PendingShipments', 'Trailer_number', 0, 'trailer_number'),
    ('PendingShipments', 'Quantity', 0, 'quantity'),
    ('PendingShipments', 'Delete_flag', 0, 'shipped'),
    ('QAQuestion', 'Question_id', 1, 'question_id'),
    ('QAQuestion', 'Question', 1, 'question'),
    ('QAQuestion', 'Enabled', 0, 'enabled'),
    ('QAHeader', 'Header_id', 1, 'header_id'),
    ('QAHeader', 'Order_id', 1, 'order_id'),
    ('QAHeader', 'Plant_id', 1, 'plant_id'),
    ('QAHeader', 'Qc_id', 0, 'qc_id'),
    ('QAHeader', 'Trailer_number', 0, 'trailer_number'),
    ('QAHeader', 'Trailer_load_time', 0, 'trailer_load_time'),
    ('QAHeader', 'Comments', 0, 'comments'),
    ('QAHeader', 'Voided', 0, 'voided'),
    ('QAHeader', 'Date_added', 0, 'date_added'),
    ('QAHeader', 'Added_by', 0, 'added_by'),
    ('QAResponse', 'Response_id', 1, 'response_id'),
    ('QAResponse', 'Header_id', 1, 'header_id'),
    ('QAResponse', 'Question_id', 1, 'question_id'),
    ('QAResponse', 'Response', 0, 'response')
    ) AS v (table_name, column_name, is_required, maps_to)
)
SELECT e.table_name, e.maps_to, e.column_name,
       CASE WHEN e.is_required = 1 THEN 'required' ELSE 'optional' END AS kind,
       CASE WHEN c.COLUMN_NAME IS NULL THEN 'MISSING' ELSE 'found' END AS status,
       c.DATA_TYPE
FROM expected e
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
       ON c.TABLE_SCHEMA = 'dbo' AND c.TABLE_NAME = e.table_name AND c.COLUMN_NAME = e.column_name
ORDER BY e.table_name, e.maps_to, status DESC;
-- A 'MISSING' optional column is fine when another spelling for the same
-- maps_to is 'found'. A concept with every spelling MISSING and kind
-- 'required' blocks the mirror: send this output back.

PRINT '========== 5. Are the id-range reads index seeks? (they should be) ==========';
WITH ids (table_name, column_name) AS (
    SELECT * FROM (VALUES
    ('Plant', 'Plant_id'),
    ('Company', 'Company_id'),
    ('Department', 'Department_id'),
    ('OrderType', 'Ordertype_id'),
    ('Status', 'Status_id'),
    ('MaterialType', 'Materialtype_id'),
    ('Material', 'Material_id'),
    ('LocationType', 'Locationtype_id'),
    ('Location', 'Location_id'),
    ('Customer', 'Customer_id'),
    ('Vendor', 'Vendor_id'),
    ('TransType', 'TransType_Id'),
    ('Order', 'Order_id'),
    ('transaction', 'Transaction_id'),
    ('Transaction_Archive', 'Transaction_id'),
    ('QC', 'Qc_id'),
    ('PendingShipments', 'Stage_id'),
    ('QAQuestion', 'Question_id'),
    ('QAHeader', 'Header_id'),
    ('QAResponse', 'Response_id')
    ) AS v (table_name, column_name)
)
SELECT ids.table_name, ids.column_name,
       i.name AS index_name, i.type_desc,
       CASE WHEN i.name IS NULL THEN 'NO INDEX LEADS WITH THIS COLUMN - reads would scan; raise with the DBA'
            ELSE 'seek - OK' END AS verdict
FROM ids
LEFT JOIN sys.tables t ON t.name = ids.table_name
LEFT JOIN sys.columns c ON c.object_id = t.object_id AND c.name = ids.column_name
LEFT JOIN sys.index_columns ic ON ic.object_id = t.object_id AND ic.column_id = c.column_id AND ic.key_ordinal = 1
LEFT JOIN sys.indexes i ON i.object_id = ic.object_id AND i.index_id = ic.index_id
ORDER BY ids.table_name;

PRINT '========== 6. Where should the companion read from? ==========';
SELECT name, is_read_committed_snapshot_on, snapshot_isolation_state_desc, recovery_model_desc
FROM sys.databases WHERE name = DB_NAME();
-- A readable secondary means the companion can read with zero load on the
-- primary. This needs VIEW SERVER STATE; "permission denied" just means ask the DBA.
SELECT ar.replica_server_name, ar.secondary_role_allow_connections_desc
FROM sys.availability_replicas ar;

PRINT '========== 7. Can this login see the user names? ==========';
SELECT COUNT(*) AS readable_user_rows FROM FECoreData.dbo.[User];
-- "Invalid object" or "permission denied" is fine: transactions are then
-- attributed to "Legacy user <id>" instead of a name.

PRINT '========== 8. What the transaction types are called ==========';
SELECT * FROM dbo.[TransType];
-- The mirror reads what each type is from its name (PROD-LOAD and MOVE-LOAD
-- are loads, SHIP-LEAVE a ship, REVERSAL undoes its parent). Send this back
-- so any name it does not recognise can be added.

PRINT '========== 9. Are quantities out of a location stored negative? ==========';
SELECT TOP 20 Transaction_id, Transtype_id, From_qty, To_qty, Parent_transaction_id
FROM dbo.[transaction]
ORDER BY Transaction_id DESC;
-- The exports show From_Qty negative (-4689 out, +4689 in). The mirror
-- measures this on every sync, but these 20 rows show it plainly.

