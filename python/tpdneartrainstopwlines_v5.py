#!/usr/bin/env python
# -*- coding: utf-8 -*-
# create txt file with tpd per line (agency_id+route_short_name) near TrainStop 
# for TrainStop, need to filter stops w tpd per line file and merge the count per line from all stops near TrainStop
#
# input:
#   parent_path = 'C:\\transitanalyst\\processed\\'
#   servicedate = '20180425'
#   train_stops_20180425.txt
#   geojson file of stops with max and average trips per day and tpd per line (agency_id+route_short_name) -'stops_w_tpd_per_line'+'_'+servicedate+'.geojson'
#
# output:
#   txt file with tpd per line (agency_id+route_short_name) near trainstop - 'trainstop_w_tpd_per_line'+'_'+servicedate+'.txt'
#   js file of stops with max and average trips per day and tpd per line (agency_id, route short name) -'trainstop_w_tpd_per_line'+'_'+servicedate+'.js'
#
print '----------------- create txt file with tpd per line (agency_id+route_short_name) near trainstop --------------------------'
print 'output txt file with tpd per line (agency_id+route_short_name) near trainstop'
from datetime import date
from datetime import timedelta
import time
import copy
import csv
import json
from geopy.distance import vincenty
import numpy as np
print "Local current time :", time.asctime( time.localtime(time.time()) )
#_________________________________
#
# input:
parent_path = 'C:\\transitanalyst\\processed\\'
servicedate = '20180425'
stopswtpdfile = 'stops_w_tpd_per_line'+'_'+servicedate+'.geojson'
trainstopsfilein = 'train_stops'+'_'+servicedate+'.txt'
# output:
trainstopwtpdperlinetxtfile = 'trainstop_w_tpd_per_line'+'_'+servicedate+'.txt'
trainstopwtpdperlinejsfile = 'trainstop_w_tpd_per_line'+'_'+servicedate+'.js'

gtfspathin = parent_path
gtfspathout = parent_path

#
# load files 
#

# >>> load stops_w_tpd_per_line geojson file 
print parent_path+stopswtpdfile
with open(parent_path+stopswtpdfile) as sf:
	stops_geo = json.load(sf)
print 'loaded stops_geo, feature count: ', len(stops_geo['features'])
#print stops_geo

# >>> load trainstops txt file 
txtfilein = trainstopsfilein
trainstops_list = []
with open(gtfspathin+txtfilein, 'rb') as f:
	reader = csv.reader(f)
	header = reader.next() # ['stop_id', 'stop_name', 'stop_lat', 'stop_lon']
	print header
	for row in reader:
		#print row
		trainstops_list.append([row[0], float(row[2]), float(row[3])])
print trainstops_list[:4]
print 'trainstops_list loaded. stop count ', len(trainstops_list)

#
# process loaded files
#

#
# recreate stop dict 
#

stops_dict = {}
for feature in stops_geo['features']:
	#print feature['geometry']
	#print feature['properties']
	stop_id = feature['properties']['stop_id']
	stop_lat = feature['geometry']['coordinates'][1]
	stop_lon = feature['geometry']['coordinates'][0]
	maxtpdatstop = feature['properties']['maxtpdatstop']
	averagetpdatstop = feature['properties']['averagetpdatstop']
	maxdaytpdperline_dict = feature['properties']['maxdaytpdperline_dict']
	
	stops_dict[stop_id] = [stop_lat, stop_lon, maxtpdatstop, averagetpdatstop, maxdaytpdperline_dict]
print 'len(stops_dict) : ', len(stops_dict)

#
# for each trainstop 
#   filter stops w tpd per line near trainstop location 
#   merge the tpd per line from all stops near trainstop
#   output merged trainstop tpd per line dict to txt file
#   collect stopsforoutput_dict for js output
#

fileout = open(gtfspathout+trainstopwtpdperlinetxtfile, 'w') # open file to save results 
postsline = 'trainstop_id,line_name,opdneartrainstop,linestopsneartrainstop,averagetpdatstop,mediantpdatstop\n'
fileout.write(postsline)

trainstopcount = 0
stopsneartrainstop = {}
stopsforoutput_dict = {}
# for each trainstop
# get trainstop location to use as filter
for [trainstop_id, trainstop_lat, trainstop_lon] in trainstops_list:
	print trainstop_lat, trainstop_lon, trainstop_id
	trainstopcount +=1
	stopsneartrainstop[trainstop_id] = []
	trainstop_loc = (trainstop_lat, trainstop_lon)

# filter stops w tpd per line near trainstop
	trainstop_stops_dict = {}
	trainstop_tpdperline_dict = {} # line_name:[totalopd,[tpd1,tpd2,tpd3...]]
	stopneartrainstopcount = 0
	opdneartrainstop = 0
	linesneartrainstop = 0
	for stop_id, [stop_lat, stop_lon, maxtpdatstop, averagetpdatstop, maxdaytpdperline_dict] in stops_dict.iteritems() :
		stop_loc = (stop_lat, stop_lon)
		distance = vincenty(stop_loc,trainstop_loc).m
		if distance < 500.0 : 
			#print stop_loc
			stopneartrainstopcount +=1
			stopsneartrainstop[trainstop_id].append(stop_id)
			#trainstop_stops_dict[stop_id] = [stop_lat, stop_lon, maxtpdatstop, averagetpdatstop, maxdaytpdperline_dict]

# merge the tpd per line from stop near trainstop
			#print maxdaytpdperline_dict
			for line_name, tpd in maxdaytpdperline_dict.iteritems() :
				clean_line_name = ''
				for i in range(len(line_name)): # stupid workaround for unicode problem of aleph in route_short_name
					lnchar = line_name[i]
					if lnchar == '-' : lnchar = '-'
					elif lnchar > '9': lnchar = 'a'
					clean_line_name += lnchar
				line_name = clean_line_name
				#print line_name
				opdneartrainstop += tpd
				if line_name not in trainstop_tpdperline_dict : # not in merged dict then add it
					#trainstop_tpdperline_dict[line_name] = tpd # merge by sum
					trainstop_tpdperline_dict[line_name] = [tpd,[tpd]] # merge by sum and collecting
					linesneartrainstop +=1
				else: # already in merged dict then sum tpd
					trainstop_tpdperline_dict[line_name][0] += tpd # merge by sum
					trainstop_tpdperline_dict[line_name][1].append(tpd) # and merge by collecting
	print 'stopneartrainstopcount, linesneartrainstop, opdneartrainstop: ', stopneartrainstopcount, linesneartrainstop, opdneartrainstop
	#print trainstop_tpdperline_dict

# output merged trainstop opportunities per day (opd) per line dict to txt file
#   and collect stopsforoutput_dict for js output
	train_tpd = 0
	total_bus_tpd = 0
	tpdperline_dict = {}
	stopsforoutput_dict[trainstop_id] = [trainstop_lat, trainstop_lon, train_tpd, total_bus_tpd, tpdperline_dict] # collect trainstop location in stopsforoutput_dict and placeholders
	#for line_name, [opd,tpdlist] in trainstop_tpdperline_dict.iteritems() :
	for line_name, [opd,tpdlist] in sorted(trainstop_tpdperline_dict.iteritems(), reverse=True, key=lambda(k,v): (v[0]/len(v[1]))):
		averagetpdperline = opd/len(tpdlist)
		#postsline = trainstop_id+','+line_name+','+str(opd)+','+str(len(tpdlist))+','+str(averagetpdperline)+','+str(np.median(np.array(tpdlist)))+',|,'+','.join(map(str,tpdlist))+'\n'
		postsline = trainstop_id+','+line_name+','+str(opd)+','+str(len(tpdlist))+','+str(averagetpdperline)+','+str(int(np.median(np.array(tpdlist))))+'\n'
		fileout.write(postsline)
		
		stopsforoutput_dict[trainstop_id][4][line_name] = averagetpdperline # collect tpdperline dict in stopsforoutput_dict
		if line_name == '2-' : # train service
			train_tpd += averagetpdperline # should be only one... but just in case...
		else : # bus service
			total_bus_tpd += averagetpdperline
			
	stopsforoutput_dict[trainstop_id][2] = train_tpd # collect in stopsforoutput_dict
	stopsforoutput_dict[trainstop_id][3] = total_bus_tpd # collect in stopsforoutput_dict
	
	print 'trainstopcount : ', trainstopcount

		
fileout.close()
print 'closed file: ', trainstopwtpdperlinetxtfile

#print stopsneartrainstop
for ts_id, s_list in stopsneartrainstop.iteritems() : 
	print ts_id, s_list

#
#   output js file of stops with max and average trips per day and tpd per line (agency_id, route short name) -'trainstop_w_tpd_per_line'+'_'+servicedate+'.js'
#
jsfileout = trainstopwtpdperlinejsfile

def getJSON(s_id):
	return {
		"type": "Feature",
		"geometry": {
			"type": "Point",
			"coordinates": [float(stopsforoutput_dict[s_id][1]),float(stopsforoutput_dict[s_id][0])]
		},
		"properties": { 
			"trainstop_id": s_id,
			"train_tpd": stopsforoutput_dict[s_id][2],
			"total_bus_tpd": stopsforoutput_dict[s_id][3],
#			"tpdperline_dict": sorted(stopsforoutput_dict[s_id][4].iteritems(), reverse=True, key=lambda(k,v): (v)) # sort changes dict to array
			"tpdperline_dict": stopsforoutput_dict[s_id][4] # no sort in py, sort in js during display
		}
	}

# saveGeoJSON

print ("Generating GeoJSON export.")
geoj = {
	"type": "FeatureCollection",
	"features": [getJSON(stop_id) for stop_id in stopsforoutput_dict]
}
print ("Saving file: " + gtfspathout+jsfileout+ " ...")
nf = open(gtfspathout+jsfileout, "w")
jsonstr = json.dumps(geoj, separators=(',',':')) # smaller file for download
outstr = jsonstr.replace('}},', '}},\n')
nf.write('var stopsWtpdperline =\n')
nf.write(outstr)
nf.close()
print ("Saved file: " + jsfileout)

