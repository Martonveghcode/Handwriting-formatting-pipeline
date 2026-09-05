#! /usr/bin/env python

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
from concurrent.futures import ThreadPoolExecutor

import numpy

# Global used by rf cost method - simply so it doesn't have to reload the random forest each time...
cost_proxy = None


def _load_frf():
  """Import the optional legacy random-forest backend only when requested."""
  from frf import frf
  return frf



def end_dist_cost(left_g, right_g, mass_weight = 1.0):
  """Given two glyphs returns the cost of putting them side by side - height difference between end points of ligaments basically. It hallucinates end points if there are no ligaments, which is stupid."""
  
  # Identify the match points - if the glyphs have links then it is these, if not find the closest point on the x axis in each case and assume its where the line ends...
  if left_g.right==None:
    # No partner - select a horizontal link...
    left_left = [left_g.most_right()[1]]
    left_right = left_left
  elif len(left_g.right[1])==0:
    # It has a partner, but no link - choose some link points...
    left_left  = [left_g.most_right()[1]]
    left_right = [left_g.right[0].most_left()[1]]
  else:
    # We have an actual link we can use, to get real values...
    left_left  = [l[0].lg.get_vertex(l[3])[1] for l in left_g.right[1]]
    left_right = [l[0].lg.get_vertex(l[4])[1] for l in left_g.right[1]]
  
  if right_g.left==None:
    # No partner - select a horizontal link...
    right_right = [right_g.most_left()[1]]
    right_left = right_right
  elif len(right_g.left[1])==0:
    # It has a partner, but no link - choose some link points...
    right_right = [right_g.most_left()[1]]
    right_left  = [right_g.left[0].most_right()[1]]
  else:
    # We have an actual link we can use, to get real values...
    right_right = [l[0].lg.get_vertex(l[3])[1] for l in right_g.left[1]]
    right_left  = [l[0].lg.get_vertex(l[4])[1] for l in right_g.left[1]]
    
  # Cost is the height differences for the match points on each side - multiple match points is a possibility...
  ret = 0.0
  ret += numpy.fabs(numpy.array(left_left).reshape((-1,1)) - numpy.array(right_left).reshape((1,-1))).min()
  ret += numpy.fabs(numpy.array(left_right).reshape((-1,1)) - numpy.array(right_right).reshape((1,-1))).min()
  
  # Also add in the absolute difference in average mass and radius...
  dr_diff = numpy.fabs(left_g.get_mass() - right_g.get_mass())
  ret += mass_weight * dr_diff.sum()
  
  return ret



def glyph_pair_feat(left_g, right_g):
  """Given two glyphs this returns their relationship feature - makes use of the average/absolute difference trick to make it appropriate for distance learning, and also matches the far vectors and near vectors for the sides of the glyphs, to make it glyph order dependent."""
  
  # Extract the features, naming them according to the relationship being explored...
  left_far, left_near = left_g.get_feat()
  right_near, right_far = right_g.get_feat()
  
  # Calculate the final feature...
  near_avg = 0.5 * (left_near + right_near)
  near_diff = numpy.fabs(left_near - right_near)
  far_avg = 0.5 * (left_far + right_far)
  far_diff = numpy.fabs(left_far - right_far)
  
  # Concatenate and return...
  return numpy.concatenate((near_avg, near_diff, far_avg, far_diff), axis=0)



def end_dist_cost_rf(left_g, right_g, mass_weight = 1.0):
  """Given two glyphs returns the cost of putting them side by side - height difference between end points of ligaments basically. It hallucinates end points if there are no ligaments, which is stupid."""
  # Check we have a random forest loaded and ready to go...
  global cost_proxy
  if cost_proxy==None:
    cost_proxy = _load_frf().load_forest('cost_proxy.rf')
  
  # Identify the match points - if the glyphs have links then it is these, if not we are going to use a random forest to guess, so give up...
  joined_up = True
  if left_g.right==None:
    joined_up = False
  elif len(left_g.right[1])==0:
    joined_up = False
  else:
    # We have an actual link we can use, to get real values...
    left_left  = [l[0].lg.get_vertex(l[3])[1] for l in left_g.right[1]]
    left_right = [l[0].lg.get_vertex(l[4])[1] for l in left_g.right[1]]
  
  if right_g.left==None:
    joined_up = False
  elif len(right_g.left[1])==0:
    joined_up = False
  else:
    # We have an actual link we can use, to get real values...
    right_right = [l[0].lg.get_vertex(l[3])[1] for l in right_g.left[1]]
    right_left  = [l[0].lg.get_vertex(l[4])[1] for l in right_g.left[1]]
    
  # Cost calculation depends if the letters are joined up or not...
  if joined_up:
    # Cost is the height differences for the match points on each side - multiple match points is a possibility...
    ret = 0.0
    ret += numpy.fabs(numpy.array(left_left).reshape((-1,1)) - numpy.array(right_left).reshape((1,-1))).min()
    ret += numpy.fabs(numpy.array(left_right).reshape((-1,1)) - numpy.array(right_right).reshape((1,-1))).min()
  else:
    # Not joined up - fall back to a random forest...
    feat = glyph_pair_feat(left_g, right_g)
    ret = cost_proxy.predict(feat[numpy.newaxis,:], 0)[0]['mean']
  
  # Also add in the absolute difference in average mass and radius...
  dr_diff = numpy.fabs(left_g.get_mass() - right_g.get_mass())
  ret += mass_weight * dr_diff.sum()
  
  return ret



def _end_dist_cost_rf_matrices(glyph_pairs):
  """Vectorised RF pair costs for dynamic-programming transitions.

  Feature construction intentionally stays in the original row-major order,
  so lazy glyph feature caches and any legacy feature randomness are populated
  exactly as they were by repeated end_dist_cost_rf calls. The expensive
  forest traversal is then performed once for the entire line.
  """
  global cost_proxy
  if cost_proxy==None:
    cost_proxy = _load_frf().load_forest('cost_proxy.rf')

  # Feature extraction is the largest remaining first-run cost. Every glyph
  # owns its feature cache and LineGraph, so distinct glyphs can safely run in
  # parallel; the native feature calculation releases the GIL while working.
  pending = []
  pending_ids = set()
  for left_glyphs, right_glyphs in glyph_pairs:
    for left_g in left_glyphs:
      for right_g in right_glyphs:
        joined_up = (left_g.right is not None and
                     len(left_g.right[1])!=0 and
                     right_g.left is not None and
                     len(right_g.left[1])!=0)
        if not joined_up:
          for glyph in (left_g, right_g):
            if glyph.feat is None and id(glyph) not in pending_ids:
              pending_ids.add(id(glyph))
              pending.append(glyph)

  try:
    workers = max(1, int(os.environ.get('OMP_NUM_THREADS', '1')))
  except ValueError:
    workers = 1
  workers = min(workers, len(pending))
  if workers>1:
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='glyph-feature') as executor:
      list(executor.map(lambda glyph: glyph.get_feat(), pending))
  else:
    for glyph in pending:
      glyph.get_feat()

  ret = [numpy.empty((len(left_glyphs), len(right_glyphs)), dtype=numpy.float32)
         for left_glyphs, right_glyphs in glyph_pairs]
  predict_at = []
  predict_feat = []

  for matrix, (left_glyphs, right_glyphs) in enumerate(glyph_pairs):
    for j, left_g in enumerate(left_glyphs):
      for i, right_g in enumerate(right_glyphs):
        joined_up = (left_g.right is not None and
                     len(left_g.right[1])!=0 and
                     right_g.left is not None and
                     len(right_g.left[1])!=0)

        if joined_up:
          # This path does not invoke the forest, so retain the existing exact
          # link-distance implementation for these comparatively rare pairs.
          ret[matrix][j,i] = end_dist_cost_rf(left_g, right_g)
        else:
          predict_at.append((matrix, j, i))
          predict_feat.append(glyph_pair_feat(left_g, right_g))

          dr_diff = numpy.fabs(left_g.get_mass() - right_g.get_mass())
          ret[matrix][j,i] = dr_diff.sum()

  if len(predict_at)!=0:
    feat = numpy.asarray(predict_feat, dtype=numpy.float32)
    predicted = cost_proxy.predict(feat)[0]['mean']
    for (matrix, j, i), value in zip(predict_at, predicted):
      ret[matrix][j,i] += value

  return ret


def glyph_pair_cost_matrices(glyph_pairs, cost_func):
  """Calculate adjacency matrices, batching a whole line when supported."""
  if len(glyph_pairs)==0:
    return []
  if cost_func is end_dist_cost_rf:
    return _end_dist_cost_rf_matrices(glyph_pairs)

  ret = []
  for left_glyphs, right_glyphs in glyph_pairs:
    matrix = numpy.empty((len(left_glyphs), len(right_glyphs)), dtype=numpy.float32)
    for j, left_g in enumerate(left_glyphs):
      for i, right_g in enumerate(right_glyphs):
        matrix[j,i] = cost_func(left_g, right_g)
    ret.append(matrix)
  return ret


def glyph_pair_cost_matrix(left_glyphs, right_glyphs, cost_func):
  """Calculate a complete adjacency matrix, batching when supported."""
  return glyph_pair_cost_matrices([(left_glyphs, right_glyphs)], cost_func)[0]



def match_links(l_glyph, r_glyph):
  """Given two glyphs this matches up their links and returns a list of link pairs to be matched. Greedy matching based on distance between match points."""
  
  # Can only stitch if we have tails on both glyphs...
  if l_glyph.right!=None and len(l_glyph.right[1])!=0 and r_glyph.left!=None and len(r_glyph.left[1])!=0:
    l_links = l_glyph.right[1]
    r_links = r_glyph.left[1]
        
    # Match up the links, to choose the best pairings (Greedy)...
    if len(l_links)==1 and len(r_links)==1:
      return [(l_links[0], r_links[0])]
    else:
      matches = []
          
      cost = numpy.zeros((len(l_links), len(r_links)), dtype=numpy.float32)
      for il in range(cost.shape[0]):
        for ir in range(cost.shape[1]):
          yl = l_links[il][0].lg.get_vertex(l_links[il][3])[1]
          yr = r_links[ir][0].lg.get_vertex(r_links[ir][4])[1]
          cost[il,ir] += numpy.fabs(yl - yr)
              
          yl = l_links[il][0].lg.get_vertex(l_links[il][4])[1]
          yr = r_links[ir][0].lg.get_vertex(r_links[ir][3])[1]
          cost[il,ir] += numpy.fabs(yl - yr)
           
      while True:
        il, ir = numpy.unravel_index(numpy.argmin(cost), cost.shape)
        if cost[il, ir]>1e99: break
             
        matches.append((l_links[il], r_links[ir]))
             
        cost[il,:] = 1e100
        cost[:,ir] = 1e100
      
      return matches
  else:
    return []



def glyph_pair_offset(left_g, right_g, offset_sd, fallback = False):
  """Returns the vertical offset between two glyphs, or None if there is no information to use. Never returns None if you set fallback to True, as it will then use a random forest."""
  matches = match_links(left_g, right_g)
    
  prec_mean = 0.0
  prec = 0.0
  
  for ml, mr in matches:
    yl = ml[0].lg.get_vertex(ml[3])[1]
    yr = mr[0].lg.get_vertex(mr[4])[1]
    offset1 = yr - yl
              
    yl = ml[0].lg.get_vertex(ml[4])[1]
    yr = mr[0].lg.get_vertex(mr[3])[1]
    offset2 = yr - yl
      
    mean = 0.5 * (offset1 + offset2)
    p = 1.0 / (offset_sd**2)
    p += 1.0 / max((0.5*numpy.fabs(offset2 - offset1))**2, 1e-5)
    
    prec_mean += mean * p
    prec += p
  
  if prec>1e-3:
    left_c = left_g.get_center()
    right_c = right_g.get_center()
  
    return ((prec_mean / prec) + (right_c[1] - left_c[1]), numpy.sqrt(1.0/prec))
    
  elif fallback:
    # Check the random forest is loaded...
    global cost_proxy
    if cost_proxy==None:
      cost_proxy = _load_frf().load_forest('cost_proxy.rf')

    # Calculate the feature to be fed into the forest...
    feat = glyph_pair_feat(left_g, right_g)
    
    # Evaluate the random forest...
    res = cost_proxy.predict(feat[numpy.newaxis,:], 0)[1]
    
    # Do some sd weirdness (convolve rf error with requested uncertainty) and return...
    prec = 1.0 / (offset_sd**2)
    prec += 1.0 / res['var']
    
    return (res['mean'], numpy.sqrt(1.0/prec))
    
  else:
    return None
