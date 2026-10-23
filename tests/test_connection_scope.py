import unittest
from sop_pipeline.agent.connection_scope import joint_fasteners,exterior_translation

class ConnectionScopeTests(unittest.TestCase):
 def test_clearance_uses_nearest_exterior_plane_on_same_axis(self):
  b={'min':[8,0,0],'max':[9,1,1]};c=[{'min':[-10,-2,-10],'max':[10,20,10]}]
  self.assertEqual(exterior_translation(b,c,0,2),[4,0,0])
  self.assertEqual(exterior_translation(b,c,1,2),[0,-5,0])
 def graph(self):
  def node(o,lo,hi):return {'occurrence_id':o,'bounds_root':{'status':'available','min':lo,'max':hi}}
  nodes=[node('base',[-10,-2,-10],[10,0,10]),node('post',[4,0,4],[6,20,6]),node('bolt',[4,-1,4],[5,1,5]),node('upper',[4,10,4],[5,12,5]),node('future-bolt',[5,-1,5],[6,1,6])]
  edges=[{'occurrences':[a,b],'assembly_reference':{'occurrence_id':b,'item_type':'SURFACE'}} for a,b in [('bolt','post/leaf'),('upper','post/leaf'),('future-bolt','beam/leaf')]]
  return {'occurrences':nodes,'constraints':edges}
 def test_contact_region_and_receiver_readiness_both_required(self):
  self.assertEqual(joint_fasteners(self.graph(),['bolt','upper','future-bolt'],'post','base',{'base','post'}),['bolt'])
 def test_no_contact_is_blocked_instead_of_nearest_part_guess(self):
  g=self.graph();g['occurrences'][1]['bounds_root']['min']=[30,0,30];g['occurrences'][1]['bounds_root']['max']=[32,20,32]
  with self.assertRaises(ValueError):joint_fasteners(g,['bolt'],'post','base',{'base','post'})
 def test_missing_native_geometry_is_blocked(self):
  g=self.graph();g['occurrences'][0]['bounds_root']['status']='unavailable'
  with self.assertRaises(ValueError):joint_fasteners(g,['bolt'],'post','base',{'base','post'})
