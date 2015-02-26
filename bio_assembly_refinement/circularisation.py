'''

Class to trim and circularise contigs with overlapping edges

Attributes:
-----------
dnaA_sequence : path to file with dnaA, refA, refB sequences (positional)
fasta_file : input fasta file
working_directory : path to working directory (default to current working directory)
contigs : dict of contigs (instead of fasta file)
alignments : pre computed alignments
dnaA_alignments : pre-computed alignments against dnaA (for testing)
overlap_offset: offset from edge that the overlap can start expressed as a % of length (default 49)
overlap_boundary_max : max boundary of overlap expressed as % of length of reference (default 50)
overlap_min_length : minimum length of overlap (default 2KB)
overlap_percent_identity : percent identity of match between ends (default 85)
dnaA_hit_percent_identity : percent identity of match to dnaA (default 80)
dnaA_hit_length_minimum : minimum acceptable hit length to dnaA expressed as % (of dnaA length) (default 95) 
debug : do not delete temp files if set to true (default false)
			  
Sample usage:
-------------
from bio_assembly_refinement import circularisation

circulariser = circularisation.Circularisation(dnaA_sequence = dnaA_file,
	                                           fasta_file = myfile.fa		
											  )
circulariser.run()

Todo:
-----
1. Consider looking for and removing adaptor sequences for all contigs before circularising
2. Consider running promer to find dnaA as it may be more conserved at the protein level than at the sequence level
3. Extend logic to encompass more edge cases 

'''

import os
import re
from pyfastaq import tasks, sequences
from pyfastaq import utils as fastaqutils
from pymummer import alignment
from bio_assembly_refinement import utils

class Circularisation:
	def __init__(self, 
				 dnaA_sequence,
				 fasta_file='file.fa', 
				 working_directory=None, 
				 contigs={},
				 alignments=[],
				 dnaA_alignments=[], # Can be used for testing 
				 overlap_offset=49, 
				 overlap_boundary_max=50, 
				 overlap_min_length=2000,
				 overlap_percent_identity=85,
				 dnaA_hit_percent_identity=80,
				 dnaA_hit_length_minimum=95,			  
				 debug=False):

		''' Constructor '''
		self.dnaA_sequence = dnaA_sequence
		self.fasta_file = fasta_file
		self.working_directory = working_directory		
		if not self.working_directory:
			self.working_directory = os.getcwd()		
		self.contigs = contigs
		self.alignments = alignments
		self.dnaA_alignments = dnaA_alignments
		self.overlap_offset = overlap_offset * 0.01
		self.overlap_boundary_max = overlap_boundary_max * 0.01
		self.overlap_min_length = overlap_min_length
		self.overlap_percent_identity = overlap_percent_identity
		self.dnaA_hit_percent_identity = dnaA_hit_percent_identity
		self.dnaA_hit_length_minimum = dnaA_hit_length_minimum * 0.01	
		self.debug = debug
		
		# Extract contigs and generate nucmer hits if not provided
		if not self.contigs:
			self.contigs = {}
			tasks.file_to_dict(self.fasta_file, self.contigs) 
		
		if not self.alignments:
			self.alignments = utils.run_nucmer(self.fasta_file, self.fasta_file, self._build_alignments_filename(), min_percent_id=self.overlap_percent_identity)
		
		self.output_file = self._build_final_filename()
		
		
	def _look_for_overlap_and_trim(self):
		''' Look for overlap in contigs. If found, trim overlap off the start. Remember contig for circularisation process '''		
# 		TODO: Optimise. Work this out when we parse alignments in clean contigs stage? Move check to pymummer?
		circularisable_contigs = []
		for contig_id in self.contigs.keys():
			acceptable_offset = self.overlap_offset * len(self.contigs[contig_id])
			boundary = self.overlap_boundary_max * len(self.contigs[contig_id])
			for algn in self.alignments:	
				if algn.qry_name == contig_id and \
				   algn.ref_name == contig_id and \
				   algn.ref_start < acceptable_offset and \
				   algn.ref_end < boundary and \
				   algn.qry_end > boundary and \
				   algn.qry_start > (algn.qry_length - acceptable_offset) and \
				   algn.hit_length_ref > self.overlap_min_length and \
				   algn.percent_identity > self.overlap_percent_identity:
					original_sequence = self.contigs[contig_id]
					self.contigs[contig_id] = original_sequence[algn.ref_end+1:algn.qry_start+1]
					circularisable_contigs.append(contig_id)
					break #Just find the biggest overlap from the end and skip any other hits
		return circularisable_contigs  
		
		
	def _circularise_and_rename(self, contig_ids):
		'''
		Create a temporary multi FASTA file with circularisable contigs 
		Run nucmer with dnaA sequences 
		For each contig, circularise (either to start at dnaA or a random gene in the case of plasmids)
		Create a new name for the contigs
		'''		
		if not self.dnaA_alignments:
			self.dnaA_alignments = utils.run_nucmer(self._build_intermediate_filename(), self.dnaA_sequence, self._build_dnaA_alignments_filename(), min_percent_id=self.dnaA_hit_percent_identity)
		
		names_map = dict()
		plasmid_count = 1
		chromosome_count = 1
		contig_ids.sort()
		 
		for contig_id in contig_ids:
			plasmid = True		   		
			for algn in self.dnaA_alignments:	
				if algn.ref_name == contig_id and \
				   algn.hit_length_ref > (self.dnaA_hit_length_minimum * algn.qry_length) and \
				   algn.percent_identity > self.dnaA_hit_percent_identity:	     
					trimmed_sequence = self.contigs[contig_id]
					plasmid = False
					
					if algn.on_same_strand():
						break_point = algn.ref_start						
					else:
						# Reverse complement sequence, circularise using new start of dnaA in the right orientation
						trimmed_sequence = trimmed_sequence.translate(str.maketrans("ATCGatcg","TAGCtagc"))[::-1]
						break_point = (algn.ref_length - algn.ref_start) - 1 #interbase

					self.contigs[contig_id] = trimmed_sequence[break_point:] + trimmed_sequence[0:break_point]		
					names_map[contig_id] = 'chromosome' + str(chromosome_count)
					chromosome_count += 1		
					break;
					
			if plasmid:
				# Choose random gene in plasmid, and circularise
				names_map[contig_id] = 'plasmid' + str(plasmid_count)
				plasmid_count += 1
				
		return names_map
		

	def _write_contigs_to_file(self, contig_ids, out_file, new_names_map=None):
		output_fw = fastaqutils.open_file_write(out_file)
		for id in contig_ids:
			if new_names_map:
				contig_name = new_names_map[id]
			else:
				contig_name = id
			print(sequences.Fasta(contig_name, self.contigs[id]), file=output_fw)
		output_fw.close()
			
			
	def get_contigs(self):
		return self.contigs
			
			
	def get_results_file(self):
		return self.output_file
		
			
	def _build_alignments_filename(self):
		return os.path.join(self.working_directory, "nucmer_all_contigs.coords")
		
		
	def _build_dnaA_alignments_filename(self):
		return os.path.join(self.working_directory, "nucmer_matches_to_dnaA.coords")
		
		
	def _build_intermediate_filename(self):
		return os.path.join(self.working_directory, "trimmed.fa")
		
			
	def _build_unsorted_circularised_filename(self):
		input_filename = os.path.basename(self.fasta_file)
		return os.path.join(self.working_directory, "unsorted_circularised_" + input_filename)	
		
	def _build_final_filename(self):
		input_filename = os.path.basename(self.fasta_file)
		return os.path.join(self.working_directory, "circularised_" + input_filename)	
			   
			   
	def run(self):
	
		original_dir = os.getcwd()
		os.chdir(self.working_directory)	
		circularisable_contigs = self._look_for_overlap_and_trim()		
		self._write_contigs_to_file(self.contigs, self._build_intermediate_filename()) # Write trimmed sequences to file
		new_names = self._circularise_and_rename(circularisable_contigs)									
		self._write_contigs_to_file(circularisable_contigs, self._build_unsorted_circularised_filename(), new_names_map=new_names) # Write circularisable contigs to new file
		tasks.sort_by_size(self._build_unsorted_circularised_filename(), self.output_file) # Sort contigs in final file according to size
		
		if not self.debug:
			utils.delete(self._build_dnaA_alignments_filename())
			utils.delete(self._build_alignments_filename())
			utils.delete(self._build_intermediate_filename())
			utils.delete(self._build_unsorted_circularised_filename())
		
		os.chdir(original_dir)