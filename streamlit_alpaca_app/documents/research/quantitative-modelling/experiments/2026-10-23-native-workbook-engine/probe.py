"""Trusted engine compatibility probe, not a model or benchmark evaluator.

Mount only declared actor files. Run with network=none, a read-only root, no
capabilities, a nonroot UID, bounded resources and a private writable output dir.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile

import uno
from com.sun.star.beans import PropertyValue
from openpyxl import load_workbook
from com.sun.star.document.MacroExecMode import NEVER_EXECUTE
from com.sun.star.document.UpdateDocMode import NO_UPDATE

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def properties(**values):
    result = []
    for name, value in values.items():
        item = PropertyValue(); item.Name = name; item.Value = value; result.append(item)
    return tuple(result)


def snapshot(path: Path):
    # Read only. Openpyxl expands shared formulas using its established reader;
    # authoring and recalculation remain LibreOffice-owned.
    workbook = load_workbook(path, data_only=False, keep_links=True)
    sheets = {}
    for sheet in workbook:
        cells = {}
        for row in sheet:
            for cell in row:
                if cell.value is None:
                    continue
                formula = None
                if cell.data_type == 'f':
                    if not isinstance(cell.value, str):
                        raise ValueError('Formula feature requires separate inspection: ' + type(cell.value).__name__)
                    formula = cell.value.removeprefix('=')
                kind = 'text' if cell.data_type in {'s', 'inlineStr'} else cell.data_type
                raw = None if formula is not None else str(cell.value)
                cells[cell.coordinate] = {'type': kind, 'value': raw, 'formula': formula}
        sheets[sheet.title] = {'state': sheet.sheet_state, 'cells': cells,
                               'merges': sorted(str(item) for item in sheet.merged_cells.ranges)}
    workbook.close()
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        parts = {label: sum(name.startswith(prefix) and name.endswith('.xml') for name in members)
                 for label, prefix in {'charts': 'xl/charts/chart', 'drawings': 'xl/drawings/drawing',
                                       'pivots': 'xl/pivotTables/pivotTable', 'external_links': 'xl/externalLinks/externalLink'}.items()}
        shapes = {}
        for name in members:
            if name.startswith('xl/drawings/drawing') and name.endswith('.xml'):
                drawing = ET.fromstring(archive.read(name))
                for element in drawing.iter():
                    tag = element.tag.rsplit('}', 1)[-1]
                    if tag in {'sp', 'pic', 'graphicFrame', 'grpSp', 'cxnSp'}:
                        shapes[tag] = shapes.get(tag, 0) + 1
        return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'sheets': sheets,
                'parts': parts, 'shape_counts': shapes}


def compare(before, after):
    changed_values, removed_formulas, changed_formulas = [], [], []
    for name, sheet in before['sheets'].items():
        target = after['sheets'].get(name, {}).get('cells', {})
        for address, cell in sheet['cells'].items():
            other = target.get(address, {})
            location = name + '!' + address
            if cell['formula'] is not None:
                if other.get('formula') is None:
                    removed_formulas.append(location)
                elif cell['formula'] != other['formula']:
                    changed_formulas.append({'cell': location, 'before': cell['formula'], 'after': other['formula']})
            elif (cell['type'], cell['value']) != (other.get('type'), other.get('value')):
                if cell['type'] == other.get('type') == 'n' and cell['value'] is not None and other.get('value') is not None:
                    if Decimal(cell['value']) == Decimal(other['value']):
                        continue
                changed_values.append({'cell': location, 'before': cell, 'after': other})
    return {'sheets_before': list(before['sheets']), 'sheets_after': list(after['sheets']),
            'parts_before': before['parts'], 'parts_after': after['parts'],
            'shapes_before': before['shape_counts'], 'shapes_after': after['shape_counts'],
            'literal_changes_count': len(changed_values), 'literal_change_examples': changed_values[:8],
            'removed_formulas_count': len(removed_formulas), 'removed_formula_examples': removed_formulas[:8],
            'changed_formula_expressions_count': len(changed_formulas), 'changed_formula_examples': changed_formulas[:8],
            'merges_changed': [name for name, sheet in before['sheets'].items()
                               if sheet['merges'] != after['sheets'].get(name, {}).get('merges')]}


def main():
    output = Path('/outputs'); output.mkdir(exist_ok=True)
    Path('/tmp/home').mkdir(exist_ok=True)
    report = {'uid': os.getuid(), 'environment_keys': sorted(os.environ), 'root_has_application': Path('/app').exists(),
              'engine_version': subprocess.check_output(['soffice', '--version'], text=True).strip(), 'workbooks': []}
    try:
        with socket.create_connection(('1.1.1.1', 443), timeout=1):
            report['network_probe'] = 'unexpected_connection'
    except OSError as exc:
        report['network_probe'] = {'errno': exc.errno, 'type': type(exc).__name__}
    office = subprocess.Popen(['soffice', '--headless', '--nologo', '--norestore', '--nodefault',
        '--nofirststartwizard', '-env:UserInstallation=file:///tmp/lo-profile',
        '--accept=pipe,name=workbook-probe;urp;StarOffice.ComponentContext'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', local)
        deadline = time.monotonic() + 20
        while True:
            try:
                context = resolver.resolve('uno:pipe,name=workbook-probe;urp;StarOffice.ComponentContext'); break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        desktop = context.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', context)
        options = properties(Hidden=True, MacroExecutionMode=NEVER_EXECUTE, UpdateDocMode=NO_UPDATE)
        for path in sorted(Path('/inputs').glob('*.xlsx')):
            document = None
            try:
                before = snapshot(path)
                document = desktop.loadComponentFromURL(path.as_uri(), '_blank', 0, options)
                document.calculateAll()
                destination = output / path.name
                document.storeAsURL(destination.as_uri(), properties(FilterName='Calc MS Excel 2007 XML', Overwrite=False))
                after = snapshot(destination)
                row = {'input': path.name, 'input_sha256': before['sha256'], 'output_sha256': after['sha256'],
                       'comparison': compare(before, after)}
                report['workbooks'].append(row)
                print(json.dumps({'input': path.name, 'comparison': row['comparison']}), flush=True)
            except Exception as exc:
                report['workbooks'].append({'input': path.name, 'status': 'failed',
                                            'error_type': type(exc).__name__, 'error': str(exc)})
            finally:
                if document is not None:
                    document.close(True)
        # An independently specified numerical calculation verifies live native
        # formulas, the enterprise/equity bridge, saving/reopening and an input
        # change. It is not a FINCH prediction or an accuracy result.
        document = desktop.loadComponentFromURL('private:factory/scalc', '_blank', 0, options)
        try:
            sheet = document.getSheets().getByIndex(0); sheet.setName('DCF')
            for cell, label, value in [('B2', 'Base free cash flow', 100), ('B3', 'FCF growth', 0.05),
                                      ('B4', 'WACC', 0.10), ('B5', 'Terminal growth', 0.025),
                                      ('B6', 'Debt', 200), ('B7', 'Cash', 50), ('B8', 'Shares', 10)]:
                sheet.getCellRangeByName('A' + cell[1:]).setString(label)
                sheet.getCellRangeByName(cell).setValue(value)
            for year, column in enumerate('BCDEF', 1):
                sheet.getCellRangeByName(column + '10').setValue(year)
                sheet.getCellRangeByName(column + '11').setFormula(f'=$B$2*(1+$B$3)^{column}10')
                sheet.getCellRangeByName(column + '12').setFormula(f'={column}11/(1+$B$4)^{column}10')
            for cell, label, formula in [('B14', 'Terminal free cash flow', '=F11*(1+$B$5)'),
                ('B15', 'Terminal enterprise value', '=B14/($B$4-$B$5)'),
                ('B16', 'Present terminal value', '=B15/(1+$B$4)^F10'),
                ('B17', 'Enterprise value', '=SUM(B12:F12)+B16'),
                ('B18', 'Equity value', '=B17-B6+B7'), ('B19', 'Value per share', '=B18/B8')]:
                sheet.getCellRangeByName('A' + cell[1:]).setString(label)
                sheet.getCellRangeByName(cell).setFormula(formula)
            document.calculateAll()
            report['dcf'] = {'base_per_share': sheet.getCellRangeByName('B19').getValue(),
                             'base_formula': sheet.getCellRangeByName('B19').getFormula()}
            destination = output / 'calculation-proof.xlsx'
            document.storeAsURL(destination.as_uri(), properties(FilterName='Calc MS Excel 2007 XML', Overwrite=False))
        finally:
            document.close(True)
        document = desktop.loadComponentFromURL(destination.as_uri(), '_blank', 0, options)
        try:
            sheet = document.getSheets().getByIndex(0)
            report['dcf']['reopened_base_per_share'] = sheet.getCellRangeByName('B19').getValue()
            sheet.getCellRangeByName('B4').setValue(0.12)
            document.calculateAll()
            report['dcf']['wacc_12_percent_per_share'] = sheet.getCellRangeByName('B19').getValue()
            report['dcf']['reopened_formula'] = sheet.getCellRangeByName('B19').getFormula()
        finally:
            document.close(True)
        report['status'] = 'engine_probe_executed_not_accepted'
    finally:
        office.terminate()
        try:
            office.wait(timeout=5)
        except subprocess.TimeoutExpired:
            office.kill(); office.wait()
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
