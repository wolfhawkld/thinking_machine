import unittest
import nonthinking_budget_analysis_20260910 as m


class ScoreTests(unittest.TestCase):
    def pair(self):
        return {'pair_id':'p','arms':{
            'context_a':{'task_id':'a','correct_option_ids':['QA']},
            'context_b':{'task_id':'b','correct_option_ids':['QB']}}}

    def test_own_full_cross_and_sign(self):
        r = lambda x:{'valid_choice':True,'selected_option_id':x}
        own=m.score_pair(self.pair(),{'a':r('QA'),'b':r('QB')})
        self.assertEqual((own['own'],own['cross'],own['full'],own['signed']),(2,0,1,2))
        cross=m.score_pair(self.pair(),{'a':r('QB'),'b':r('QA')})
        self.assertEqual((cross['own'],cross['cross'],cross['full'],cross['signed']),(0,2,0,-2))

    def test_invalid_and_missing_keep_pair_denominator(self):
        r=m.score_pair(self.pair(),{'a':{'valid_choice':False,'selected_option_id':'QA'}})
        self.assertEqual((r['own'],r['cross'],r['full'],r['missing'],r['invalid'],r['tie']),(0,0,0,1,1,1))


if __name__ == '__main__': unittest.main()
