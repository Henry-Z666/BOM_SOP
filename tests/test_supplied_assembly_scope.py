import unittest
from dataclasses import replace
from sop_pipeline.agent.bom_normalizer import NormalizedBom,NormalizedBomRow,assembly_scope_frontier,supplied_assembly_owners

def row(n,level,marked=False):
 return NormalizedBomRow(n,level,str(n),'D'+str(n),'物料','','', '件','','','',None,False,assembly_boundary=marked,level_fill_rgb='FFFF00' if marked else '')

def bom(rows):return NormalizedBom('normalized-bom/v1','MBOM',1,{},tuple(rows),('MBOM',))

class SuppliedAssemblyScopeTests(unittest.TestCase):
 def test_preassembled_column_excludes_internal_white_and_nested_marked_rows(self):
  b=bom([row(2,'0'),row(3,'0.1'),row(43,'0.1.2',True),row(44,'0.1.2.1'),row(49,'0.1.2.2',True),row(50,'0.1.2.2.1'),row(58,'0.1.3',True),row(59,'0.1.3.1')])
  self.assertEqual([r.row for r in assembly_scope_frontier(b).rows],[2,3,43,58])
  self.assertEqual(supplied_assembly_owners(b),{44:43,49:43,50:43,59:58})
 def test_sibling_fastener_retained_and_decimal_prefix_not_confused(self):
  b=bom([row(1,'0'),row(2,'0.1.2',True),row(3,'0.1.2.1'),row(4,'0.1.20'),row(5,'0.1.36',True)])
  self.assertEqual([r.row for r in assembly_scope_frontier(b).rows],[1,2,4,5])
 def test_unmarked_source_keeps_previous_scope(self):
  b=bom([row(1,'0'),row(2,'0.1'),row(3,'0.1.1')]);self.assertEqual(assembly_scope_frontier(b),b)
 def test_same_material_identity_does_not_merge_distinct_issued_positions(self):
  b=bom([row(1,'0'),row(2,'0.1',True),row(3,'0.1.1'),replace(row(4,'0.2',True),material_code='2'),row(5,'0.2.1')])
  self.assertEqual([r.row for r in assembly_scope_frontier(b).rows],[1,2,4])
