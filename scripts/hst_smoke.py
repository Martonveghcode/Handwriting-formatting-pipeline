from __future__ import print_function

import json
import os
import random
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.getcwd())

import costs
from chunk_db import ChunkDB
from generate import (combine_seperate, layout_draw, render, select_glyphs_dp,
                      stitch_connect)
from glyph_db import GlyphDB
from line_graph.line_graph import LineGraph
from texture_cache import TextureCache


def main(source, output):
  random.seed(7)
  np.random.seed(7)
  started = time.perf_counter()

  glyphs = GlyphDB()
  glyph_count = glyphs.add(source)
  selected = select_glyphs_dp('test', glyphs, 16, 600.0, 120.0, 0.3,
                              costs.end_dist_cost_rf, False)
  positioned = layout_draw(selected, glyphs, (10.0, 3.0, 3.0, 0.01), 0.5)
  connected = stitch_connect(positioned, True, False, 0)
  line = combine_seperate(connected)

  scale = np.eye(3, dtype=np.float32)
  scale[2, 2] /= 195.0
  line.transform(scale, True)
  combined = LineGraph()
  combined.from_many(line)

  textures = TextureCache(256, 2 * 1024**3)
  chunks = ChunkDB()
  chunk_count = chunks.add(source)
  chunks.set_params(4, 1.0, 2.0, 4.0)
  combined = chunks.convert(combined, 12, True, textures, 4)

  profile = {}
  image, cuts = render(combined, 8, textures, 3, 12.0, 0.5, 0.5, 2.0,
                       1.0, 1.0, 0.05, True, profile)
  Image.fromarray(image[:, :, [2, 1, 0, 3]], 'RGBA').save(output)
  print(json.dumps({
      'glyphs_loaded': glyph_count,
      'chunks_loaded': chunk_count,
      'graph_cuts': cuts,
      'image_shape': list(image.shape),
      'render_profile': profile,
      'texture_cache': textures.stats(),
      'end_to_end_seconds': time.perf_counter() - started,
      'output': output,
  }, indent=2, sort_keys=True))


if __name__ == '__main__':
  main(sys.argv[1], sys.argv[2])
