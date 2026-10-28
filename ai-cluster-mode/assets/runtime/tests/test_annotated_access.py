import unittest
from cluster.research import normalize_metadata

class AnnotatedAccessTest(unittest.TestCase):
    def test_access_note_and_snapshot_date_preserved_without_invented_time(self):
        x={'sources':[{'source_id':'S1','accessed_at':'2026-09-30（本轮活页读取）'},
                      {'source_id':'S2','accessed_at':'2026-08-19快照（本轮核对冻结PDF）'}]}
        adapted,changes=normalize_metadata(x,'kimi',boolean_strings=True)
        self.assertEqual(len(changes),2)
        for old,new in zip(x['sources'],adapted['sources']):
            self.assertIsNone(new['accessed_at']);self.assertEqual(new['reported_access_description'],old['accessed_at'])
            self.assertNotIn('reported_accessed_at',new)
        self.assertEqual(x['sources'][0]['accessed_at'],'2026-09-30（本轮活页读取）')
    def test_legacy_unrecognized_and_bad_date_are_not_auto_repaired(self):
        x={'sources':[{'accessed_at':'2026-09-30（本轮活页读取）'}]}
        self.assertEqual(normalize_metadata(x)[0],x)
        for value in ['now','2026-99-30（已读）','2026-09-30T10:00:00']:
            x={'sources':[{'accessed_at':value}]};self.assertEqual(normalize_metadata(x,boolean_strings=True)[0],x)
    def test_explicit_canonical_display_note_does_not_remain_clickable_url(self):
        original='https://chemistry.example.edu/degrees/chembio（canonical，node/377）'
        x={'sources':[{'url':original}]};adapted,changes=normalize_metadata(x,boolean_strings=True)
        self.assertEqual(adapted['sources'][0]['url'],'https://chemistry.example.edu/degrees/chembio')
        self.assertEqual(adapted['sources'][0]['reported_url'],original);self.assertEqual(normalize_metadata(x)[0],x)
        literal={'sources':[{'url':'https://example.edu/资料（课程说明）'}]}
        self.assertEqual(normalize_metadata(literal,boolean_strings=True)[0],literal)

if __name__=='__main__':unittest.main()
