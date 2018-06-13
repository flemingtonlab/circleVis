from __future__ import print_function
import sys
import os
import sqlite3
from matplotlib.path import Path
import matplotlib.patches as patches
from numpy import arange, linspace
import matplotlib.pyplot as plt
import argparse
from collections import defaultdict
import webbrowser
import re


def parse_args():
   
    parser = argparse.ArgumentParser(description='Plot transcript')
    parser.add_argument("-is", "--intron-scale", type=float, help="The factor by which intron white space should be reduced", metavar='')
    parser.add_argument("-db", "--database", nargs='*', required=True, type=str, help="Path to each sample database file (created using build_db", metavar='')
    parser.add_argument("-c", "--color", default="#C21807", type=str, help='Exon color. Hex colors (i.e. "\#4286f4". For hex, an escape "\" must precede the argument) or names (i.e. "red")', metavar='')
    parser.add_argument("-t", "--transcript",  type=str, help='Name of transcript to plot', metavar='')
    parser.add_argument("-g", "--gene", type=str, help='Name of gene to plot (overrides "-t" flag). Will plot the longest transcript derived from that gene', metavar='')
    #parser.add_argument("-f", "--filter", action='store_true', help='Filter out non-exonic junctions', metavar='')
    parser.add_argument("-n", "--normalize", action='store_true', help='Normalize coverage between samples')
    parser.add_argument("-rc", "--reduce_canonical", type=int, help='Factor by which to reduce canonical curves', metavar='')
    parser.add_argument("-rbs", "--reduce_backsplice", type=int, help='Factor by which to reduce backsplice curves', metavar='')
    parser.add_argument("-ro", "--repress_open", action='store_true', help='Do not open plot in browser (only save it)')
    args = parser.parse_args()

    for path in args.database: 
        if not os.path.exists(path):
            sys.exit("Database: {} was not found".format(path))

    if not (args.gene or args.transcript): 
        sys.exit('Either a gene or a transcript must be specified. (ex. "-t ENST00000390665" or "-g EGFR")')

    elif not args.transcript:
        args.transcript = get_transcript_from_gene(db_path=args.database[0], gene=args.gene)

    args.color = to_rgb(args.color)

    return args


def get_transcript_from_gene(db_path, gene):

    '''Return the longest isoform of a given gene   
    If a gene name is provided (with the '-g' flag), the longest transcript/isoform of that gene is used for the plot. '''

    query = (gene, )   
    transcripts = defaultdict(int)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    fetched = c.execute('SELECT transcript, start, stop FROM coverage WHERE gene=?', query)
    
    # Calculate transcript lengths
    for i in fetched:
        transcript, start, stop = i
        transcripts[transcript] += (1 + (stop - start))
    conn.close()

    # Determine name of the longest transcript
    m = 0
    longest = ''
    for transcript, length in transcripts.items():
        if length > m:
            m = length
            longest = str(transcript)
            
    return longest


def get_exons(db_path, transcript):   
    # Takes transcript ID and returns exon coordinates - chromosome, [starts], [stops], strand
   
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    query = (transcript, )
    fetched = c.execute('SELECT chromosome,start,stop,strand FROM coverage WHERE transcript=?', query)
    try:
        chromosomes, starts, stops, strands = map(list,zip(*fetched.fetchall()))
    except ValueError:
        sys.exit("Transcript not found")     
    
    if len (set(chromosomes)) > 1:
        print("Error: {transcript} found on more than one chromosome:\n{chromosome}".format(
            transcript=transcript, chromosome='\n'.join(chromosomes) ) )
        sys.exit(1)
    
    chromosome = chromosomes[0]
    strand = strands[0]
    conn.close()

    return chromosome, starts, stops, strand


def get_coverage(db_path, transcript):
    # Takes transcript and returns coverage from db file for each exon

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    query = (transcript, )
  
    fetched = c.execute('SELECT coverage FROM coverage WHERE transcript=?', query).fetchall()
    return [i[0] for i in fetched]


def get_sj(db_path, table_name, chromosome, start, stop, strand):
    ''' Takes chromosomal coordinates and returns junctions in that range. 
        Use for both canonical and backsplice junctions. Name of table with canonical junctions: "canonical", backsplice: "circle" '''

    query = (chromosome, start, stop, strand)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    junctions = c.execute('SELECT start, stop, counts FROM %s WHERE chromosome=? and start>=? and stop<=? and strand=?' % table_name, query).fetchall()
    conn.close()

    return junctions

 
def draw_backsplice(ax, start, stop, y, adjust, bezier_offset, gene_size):
    ''' Takes a start and a stop coordinate and generates a bezier curve underneath exons (for circle junctions).
        "bezier_offset" controls the depth of the circle junctions on the plot. Default is 0.15. '''


    ylim = ax.get_ylim()
    space = ylim[1] - ylim[0]
    size_adjust = gene_size / 20
    
    verts = [
        (start, y),  # P0
        (start - size_adjust, y - (bezier_offset * space) -  adjust), # P1
        (stop + size_adjust, y - (bezier_offset * space) - adjust), # P2
        (stop, y), # P3
        ]

    codes = [Path.MOVETO,
             Path.CURVE4,
             Path.CURVE4,
             Path.CURVE4,
             ]

    path = Path(verts, codes)

    patch = patches.PathPatch(path, facecolor='none', lw=1, alpha=.1, ec='0')  ##Circle junction 'lw' = line width, alpha = transparency
    ax.add_patch(patch)


def plot_exons(ax, coordinates, y, height, colors):
    # Takes coordinates and coverage and plots exons.

    for (start, stop), color in zip(coordinates, colors):
        length = stop - start
        rect = patches.Rectangle((start, y), length, height, facecolor=color, edgecolor='k', linewidth=.5)
        ax.add_patch(rect)


def plot_SJ_curves(ax, coordinates, y):
    # Takes coordinates and plots canonical junctions above exons

    # Curve line properties
    linewidth = 1 
    alpha = 0.2

    # Curve depth (radians)
    radian_low = 0.2
    radian_high = 0.4
    radian_diff = radian_high - radian_low
    step_denominator = 1 / radian_diff

    # Length of plot
    xmin, xmax = ax.get_xlim()
    xlen = xmax - xmin

    # Plots each junction
    for start, stop, counts in coordinates:
        if counts != 0:
            radian_corr = 1
            arc_len = stop - start  

            # Depending on arc length, apply a radian correction (prevent arc exceeding y axis limits)
            if arc_len > (xlen / 8):
                radian_corr = (2 *arc_len/xlen)
            if arc_len > (xlen / 4):
                radian_corr = (1.5*arc_len/xlen)
            if arc_len > (xlen / 2):
                radian_corr = (arc_len/xlen)
            if arc_len > (xlen / 1.2):
                radian_corr = (0.4*arc_len/xlen)

            # Plots each junction, increasing radians by amount defined by counts. (More counts = smaller difference in radians per curve plotted)
            step = 1 / (counts * step_denominator)
            for radians in arange(radian_low, radian_high, step):
                ax.add_patch(patches.ConnectionPatch((start, y), (stop, y), coordsA='data',
                 connectionstyle=patches.ConnectionStyle("Arc3, rad=-"+str(radians * radian_corr)), linewidth=linewidth, alpha=alpha))


def plot_circles(ax, coordinates, y, gene_size):
    # Takes list of coordinate tuples (start, stop, counts) and plots backsplice curves using draw_backsplice()

    for start, stop, counts in coordinates:
        if counts != 0:
            step = 1 /(counts * 2)
            for num in arange(0.0, 0.5, step):
                draw_backsplice(ax=ax, start=start, stop=stop, y=y, adjust=num, bezier_offset=.15, gene_size=gene_size)


def scale_introns(coords, scaling_factor):
    # Reduces intron size, returns new exon coordinates

    if scaling_factor == 0:
        print("Intron scaling factor of 0 not allowed. Plotting without scaling.")
        return coords

    newcoords = []
    newcoords.append(coords[0])
    
    for i in range(1, len(coords)):
        length = coords[i][1] - coords[i][0] 
        exonEnd = coords[i-1][1] 
        nextExonStart = coords[i][0] 
        intron = (nextExonStart - exonEnd) / scaling_factor 
        left = newcoords[i-1][1] + intron
        right = left + length
        newcoords.append((left, right)) 

    return newcoords


def transform(original, scaled, query):
    
    ''' Transform query to new scale. 
        Adapted from https://stackoverflow.com/questions/929103/convert-a-number-range-to-another-range-maintaining-ratio'''


    orig_c = [i for j in original for i in j]
    scal_c = [i for j in scaled for i in j]

    for i in range(len(orig_c) - 1):
        left, right = orig_c[i:i+2]
        if left <= query <= right:
            break

    if len(scal_c) > i + 2:
        n_left, n_right = scal_c[i:i+2]
    else:
        n_left = scal_c[i]
        n_right = query

    n_range = n_right - n_left
    o_range = right - left 

    if o_range == 0:
        return n_left

    return (((query - left) * n_range) / o_range) + n_left


def scale_coords(oldranges, newranges, coords):
    # Scale junction coordinates to new exon coordinates using scale()

    newcoords = []
    for start, stop, counts in coords:
        newstart = transform(oldranges, newranges, start)
        newstop = transform(oldranges, newranges, stop)
        newcoords.append((newstart, newstop, counts))
    
    return newcoords


def to_rgb(color):
    # Converts hex or color name to rgb. Coverage is set up to be represented by 'alpha' of rgba

    colordict = {
        'red': '#FF0000',
        'blue': '#0000FF',
        'green': '#006600',
        'yellow': '#FFFF00',
        'purple': '#990099',
        'black': '#000000',
        'white': '#FFFFFF',
        'orange': '#FF8000',
        'brown': '#663300'
    }

    if type(color) != str:
        print("Invalid color input: %s\n Color is set to red" % color)
        return (1,0,0)
    
    if color[0] != '#' or len(color) != 7:
        if color in colordict:
            color = colordict[color.lower()]
        else:
            print("Invalid color input: %s\n Color is set to red" % color)
            return (1,0,0)

    try: 
        rgb = tuple([int(color[i:i+2], 16)/255 for i in range(1, len(color), 2)])

    except ValueError:
        print("Invalid hex input: %s. Values must range from 0-9 and A-F.\n Color is set to red" % color)
        return (1,0,0)

    return rgb


def add_ax(num_plots, n, sample_ind):
    # Add new plot

    name, canonical, backsplice, _, colors = samples[sample_ind]

    # Center the plot on the canvas
    ax = plt.subplot(num_plots, 1, n)
    ybottom = 0.5
    height = 0.5
    ytop = ybottom + height
    transcript_start = min([i[0] for i in coords]) 
    transcript_stop = max([i[1] for i in coords])  
    gene_length = transcript_stop - transcript_start
    x_adjustment = 0.05 * gene_length
    y_adjustment = ytop * height * 3
    xmin = transcript_start - x_adjustment
    xmax = transcript_stop + x_adjustment
    ymin = ybottom - y_adjustment
    ymax = ytop + y_adjustment
    ax.set_xlim([xmin, xmax])
    ax.set_ylim([ymin, ymax])
    ax.axes.get_yaxis().set_visible(False)
    ax.axes.get_xaxis().set_visible(False)
    plot_exons(ax=ax, coordinates=coords, colors=colors, height=height, y=ybottom)
    plot_SJ_curves(ax=ax, coordinates=canonical, y=ytop)
    plot_circles(ax=ax, coordinates=backsplice, y=ybottom, gene_size=gene_length)
    name = re.sub(r'[-_|]',' ', name)
    ax.set_title(name)

if sys.version_info[0] < 3:
        raise Exception("Must be using Python 3")

args = parse_args()
transcript = args.transcript
chromosome, starts, stops, strand = get_exons(args.database[0], transcript)
transcript_start = min(starts)
transcript_stop = max(stops)
coords = list(sorted(zip(starts, stops)))


if args.intron_scale:
    factor = args.intron_scale
    scaled_coords = scale_introns(coords, factor)


samples = []

for db_path in args.database:

    name = db_path
    name = db_path.split('.')[0].upper()
    canonical = get_sj(db_path=db_path, table_name="canonical", chromosome=chromosome,
                            start=transcript_start, stop=transcript_stop, strand=strand)
    backsplice = get_sj(db_path=db_path, table_name="circle", chromosome=chromosome, 
                            start=transcript_start, stop=transcript_stop, strand=strand)
    coverage = get_coverage(db_path=db_path, transcript=transcript)

    if strand == '-':
       coverage.reverse()

    if args.intron_scale:
        canonical = scale_coords(coords, scaled_coords, canonical)
        backsplice = scale_coords(coords, scaled_coords, backsplice)

    if args.reduce_canonical:
        canonical = [(i,j,k // args.reduce_canonical) for i,j,k in canonical]
    if args.reduce_backsplice:
        backsplice = [(i,j,k // args.reduce_backsplice) for i,j,k in backsplice]

    samples.append((name, canonical, backsplice, coverage))

if args.intron_scale:
    coords = scaled_coords

if args.normalize:
    highest = 0

    for index in range(len(samples)):

        coverage = samples[index][3]
        max_coverage = max(coverage)
        if max_coverage > highest:
            highest = max_coverage

for index in range(len(samples)):
    coverage = samples[index][3]
    if args.normalize:
        max_coverage = highest
    else:
        max_coverage = max(coverage)
    if max_coverage != 0:
        color = [args.color + (i/max_coverage,) for i in coverage]
    else:
        color = [args.color + (0,) for i in coverage] 
    samples[index] += (color,)


# Plot for each sample
num_plots = len(args.database)
fig = plt.figure(figsize=(15, 3 * num_plots))
for i in range(len(samples)):
    add_ax(num_plots, i + 1, i)

plt.subplots_adjust(hspace=0.4, top=0.8, bottom=0.2)
if args.gene:
    title = "%s (%s)" % (args.gene, transcript)
else:
    title = transcript

plt.suptitle(title, fontsize=16, fontweight ='bold')
plt.savefig("%s.svg" % title)

html_str = '''
<html>
<body>
<img src="%s.svg" alt="%s">
</body>
</html>
'''

with open("%s.html" % title,"w") as html:
    html.write(html_str % (title, title))

if not args.repress_open:
    webbrowser.open('file://'+os.path.realpath("%s.html" % title))
