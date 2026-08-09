import unittest

from feline.gui.controller import ChartBuffer, shifted_x_range, should_follow_candle, visible_candle_y_range


def candle(open_time, seconds, low, high):
    return {"timeframe":"1m","open_timestamp":open_time,"close_timestamp":open_time+seconds,"open":low,"high":high,"low":low,"close":high,"volume":0}


class ChartFollowTests(unittest.TestCase):
 def test_shift_preserves_width_and_uses_timeframe_interval(self):
    for timeframe,seconds in (("1m",60),("5m",300),("15m",900),("1h",3600)):
        shifted=shifted_x_range((1000,1600),timeframe)
        self.assertEqual(shifted,(1000+seconds,1600+seconds))
        self.assertEqual(shifted[1]-shifted[0],600)

 def test_visible_bounds_ignore_offscreen_and_add_padding(self):
    candles=[candle(0,60,1,9),candle(100,60,10,20),candle(160,60,12,18),candle(300,60,-5,50)]
    bounds=visible_candle_y_range(candles,(100,220))
    self.assertAlmostEqual(bounds[0],9.6)
    self.assertAlmostEqual(bounds[1],20.4)

 def test_tiny_range_is_safe_and_empty_is_noop(self):
    bounds=visible_candle_y_range([candle(100,60,1,1)],(100,160))
    self.assertLess(bounds[0],1);self.assertGreater(bounds[1],1)
    self.assertIsNone(visible_candle_y_range([candle(0,60,1,2)],(100,200)))

 def test_disabled_or_same_candle_does_not_follow(self):
    self.assertFalse(should_follow_candle(False,True,100,"EURUSD","1m","EURUSD","1m"))
    self.assertFalse(should_follow_candle(True,False,100,"EURUSD","1m","EURUSD","1m"))
    self.assertFalse(should_follow_candle(True,True,None,"EURUSD","1m","EURUSD","1m"))
    self.assertTrue(should_follow_candle(True,True,100,"EURUSD","1m","EURUSD","1m"))

 def test_chart_buffer_distinguishes_append_from_update(self):
    buffer=ChartBuffer();first=candle(100,60,1,2);updated={**first,"high":3,"close":2.5}
    self.assertTrue(buffer.add_candle(first))
    self.assertFalse(buffer.add_candle(updated))
    self.assertEqual(len(buffer.candles["1m"]),1)
    self.assertEqual(buffer.candles["1m"][-1]["high"],3)
    self.assertTrue(buffer.add_candle(candle(160,60,2,4)))


if __name__=="__main__":unittest.main()
