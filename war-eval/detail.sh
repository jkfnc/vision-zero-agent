#!/bin/bash
# detail.sh film clip... -> film/det_XXX.jpg (3fps, 5x3 tiles)
d=$1; shift
for i in "$@"; do n=$(printf %03d $i); ffmpeg -v error -y -i $d/seg_$n.mp4 -vf "fps=3,scale=256:-2,tile=5x3" -frames:v 1 $d/det_$n.jpg; done
