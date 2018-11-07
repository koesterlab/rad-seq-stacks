# -*- coding: utf-8 -*- 
"""Evaluate a stack mapping versus a simulated rage dataset.
"""
import argparse
import csv
import yaml
import time
import sys

import dinopy as dp
import pysam
from collections import Counter, namedtuple
from functools import partial

import sketching as sk


TSVRecord = namedtuple("TSVRecord", ["locus_id", "seq", "nr_parents", "nr_snps", "snps", "nr_alleles", "genotypes"])
GTRecord = namedtuple("GTRecord", ["name", "seq", "mutations", "id_reads", "dropout"])


def parse_rage_gt_file(args):
    """Read in a RAGE ground truth file.
    
    Returns:
        list: of GTRecord named tuples each of which hash the entries
        'name', 'seq', 'mutations', id_reads', 'dropout'
    """
    with open(args.yaml, 'r') as stream:
        try:
            # read all documents in the data
            inds, loci, *_, hrls = list(yaml.load_all(stream))
        except yaml.YAMLError as exc:
            print(exc)
    all_snps = []
    all_insertions = []
    all_deletions = []
    nr_added_muts = 0
    nr_added_snps = 0
    nr_added_inserts = 0
    nr_added_deletions = 0

    loc_seqs = []
    for name, locus in loci.items():
        dropout = []
        # this is the format in which stacks saves the consensus seqeunce in the tsv format
        seq = locus["p5 seq"] + dp.reverse_complement(locus["p7 seq"])
        mutations = set()
        for n, ind in locus["individuals"].items():
            if ind:
                # dropout events get an empty dict,
                # hence everything that does not evaluate to False
                # is a valid entry with one or two alleles
                for _, allele in ind.items():
                    if allele["mutations"]:
                        mutations |= set(allele["mutations"]) # extend set
                        # print("Unique mutations for {}: {}".format(name, allele["mutations"]))
                        # for mut in allele["mutations"]:
                        #     if ">" in mut:
                        #         all_snps.append(mut)
                        #     elif "-" in mut:
                        #         all_deletions.append(mut)
                        #     elif "+" in mut:
                        #         all_insertions.append(mut)
                        #     else:
                        #         raise ValueError("Invalid mutation", mut)
                    
                dropout.append(False)
            else:
                dropout.append(True)

        # compile and append a record for this locus
        id_reads = locus["id reads"]
        gt_record = GTRecord(name, seq, mutations, id_reads, dropout)
        nr_added_muts += len(mutations)
        nr_added_snps += len([mut for mut in mutations if ">" in mut])
        nr_added_inserts += len([mut for mut in mutations if "+" in mut])
        nr_added_deletions += len([mut for mut in mutations if "-" in mut])
        loc_seqs.append(gt_record)
    # print("found {} SNP, {} insertions, and {} deletions.".format(len(all_snps), len(all_insertions), len(all_deletions)))
    # print(all_snps)
    print("Nr of added muts:", nr_added_muts)
    print("Nr of added snps:", nr_added_snps)
    print("Nr of added inserts:", nr_added_inserts)
    print("Nr of added deletions:", nr_added_deletions)
    # time.sleep(60)
    time.sleep(5)
    return loc_seqs


# def get_stacks_data(args):
#     """Read in the export.tsv file of a stacks dataset.

#     Returns:
#         list: of TSVRecord entries. Each of these has the following attributes:
#         'locus_id', 'seq', 'nr_parents', 'nr_snps', 'snps', 'nr_alleles', 'genotypes'.
#     """
#     loc_seqs = []
#     with open(args.stackstsv, "r") as stacks_file:
#         for line in csv.reader(stacks_file, delimiter="\t"):
#             if line and line[0].isdigit():
#                 try:
#                     loc_id, _, _, _, seq, nr_par, _, nr_snps, snps, nr_alles, _, _, i1, i2, i3, i4, i5, i6 = line
#                     record = TSVRecord(loc_id, seq, nr_par, nr_snps, snps, nr_alles, [i1, i2, i3, i4, i5, i6])
#                     loc_seqs.append(record)
#                 except:
#                     print(len(line), line)
#                     raise
#             # else:
#             #     # handle header line, empty lines, individual definition etc.
#             #     print("Could not parse", line)
                
#     # print("Found {} stacks loci".format(len(loc_seqs)))
#     return loc_seqs



def get_stacks_data(args):
    loc_seqs = []
    haplotypes_file = pysam.VariantFile(args.stacks_haplo, 'r')
    indexed_far = dp.FastaReader(args.stacks_fa)

    for variant_record in haplotypes_file:
        chromosome = variant_record.chrom
        one_based_pos = variant_record.pos
        reference_allele, *alternate_alleles = variant_record.alleles
        
        print(f"{chromosome}@{one_based_pos} {reference_allele}>{''.join(alternate_alleles)}")
        # fetch assembly from index fasta file
        seq = list(indexed_far[chromosome])[0].sequence
        # found_base = chr(seq[one_based_pos-1])
        # print(found_base)
        individuals = [None]
        record = TSVRecord(chromosome, seq, None, len(alternate_alleles), f"{one_based_pos},{reference_allele}>{''.join(alternate_alleles)}", len(variant_record.alleles), individuals)
        loc_seqs.append(record)
        print("info:", [(key, value) for key, value in variant_record.info.items()])
        print("samples:")
        for s, vals in variant_record.samples.items():
            print(s, [(key, value) for key, value in vals.items()])
        # print(record)
        print()
        
    print("Found {} stacks loci with SNPs".format(len(loc_seqs)))



def compare_assemblies(gt_data, stacks_data, similarity=0.2, verbose=True):
    """Compare clusterings with minhash sketching.

    Arguments:
        gt_data (list of GTRecords): From a RAGE _gt.yaml file.
        stacks_data (list of TSVRecords): From a Stacks _export.tsv file.
        similarity (float): Similarity threshold for the identification of
            similar sequences. Default: 0.3

    Returns:
        dict: Mapping a tuple of (RAGE locus name, RAGE locus reference sequence) to 
        the RAGE locus record and a list of associated Stacks locus records.
    """
    # sketching parameters
    k = 6
    s = 400
    sketch = partial(sk.bottom_sketch, k=k, s=s)
    # initialize assembly
    assembly = {(record.name, record.seq): (record, []) for record in gt_data}
    print("Sketching stacks data")
    # pre-sketch the data to avoid quadratic (re-) sketching
    sketched_stacks_data = [(sketch(record.seq), record) for record in stacks_data]
    print("Searching")
    for index, (gt_record) in enumerate(gt_data):
        # print user output
        if verbose and index % 100 == 0:
            print(index)
        s_gt = sketch(gt_record.seq)
        # compare all stacks locus sketches against the active RAGE locus sketch
        # Append the loci of all similar sequences to the assembly list.
        for sketch_stacks, record in sketched_stacks_data:
            if sk.compare_sketches(s_gt, sketch_stacks) > similarity:
                assembly[(gt_record.name, gt_record.seq)][1].append(record)
    return assembly


def evaluate_assembly(assembly, gt_data, stacks_data, args):
    """Analyze an assembly of RAGE vs Stacks data.

    Arguments:
        assembly (dict): Mapping of RAGE sequences to RAGE records and associated Stacks records.
        gt_data (list of GTRecords): From a RAGE _gt.yaml file.
        stacks_data (list of TSVRecords): From a Stacks _export.tsv file.
        args (argparse.NameSpace): User parameters.
    """

    # write mapping to file
    if args.output:
        with open(args.output, "w") as outfile:
            for (gt_name, gt_seq), (gt_locus, stacks_loci) in assembly.items():
                print(gt_name, file=outfile)
                print("      ", gt_seq, file=outfile)
                for record in stacks_loci:
                    print("{:>6}".format(record.locus_id), record.seq, file=outfile)
                print("\n", file=outfile)


    # identify length profile of the mapping
    # i.e. how many Stacks loci were assigned to each RAGE locus
    locus_length_counter = Counter()
    locus_id_counter = Counter()
    split_loci, id_loci = 0, 0
    total_len = 0
    for (gt_name, gt_seq), (gt_locus, stacks_loci) in assembly.items():
        loc_len = len(stacks_loci)
        total_len += loc_len
        locus_length_counter[loc_len] += 1
        if loc_len > 1:
            split_loci += 1
            if gt_locus.id_reads > 0:
                id_loci += 1
                locus_id_counter[loc_len] += 1
    print("Total length: ", total_len)
    print("Counts of how many stacks loci were assigned to a RAGE locus:\n  {}".format(sorted(locus_length_counter.items())))
    print("Of {} split up loci, {} had ID reads.\n  {}\n".format(split_loci, id_loci, locus_id_counter))
    
    # Check to how many RAGE loci each Stacks loci was assigned.
    # This should always be 1.
    # A value >1 would suggest that a stacks locus is similar to two or
    # more RAGE loci, which is highly unlikely
    locus_assignment_counter = Counter()
    for (gt_name, gt_seq), (gt_locus, stacks_loci) in assembly.items():
        for locus in stacks_loci:
            locus_assignment_counter[locus.locus_id] += 1
    print("The 5 most assigned stacks locus id. This should always be 1:\n  {}\n".format(locus_assignment_counter.most_common(5)))

    # find stacks loci that are not in the RAGE loci (singletons and HRLs?)
    occurence_count = Counter({rec.locus_id : 0 for rec in stacks_data})
    for (gt_name, gt_seq), (gt_locus, stacks_loci) in assembly.items():
        for locus in stacks_loci:
            occurence_count[locus.locus_id] += 1
    
    # Assemble previously unassigned loci
    loci_to_postprocess = []
    for record in stacks_data:
        if occurence_count[record.locus_id] == 0:
            loci_to_postprocess.append(record)
    nr_unassigned = sum([1 if count == 0 else 0 for _, count in occurence_count.items()])
    print("Number of stacks loci that were not associated with a rage locus: {}".format(nr_unassigned))

    # create a secondary assembly with a lower similarity threshold.
    secondary_sim = 0.2
    secondary_assembly = compare_assemblies(gt_data, loci_to_postprocess, similarity=secondary_sim, verbose=False)
    kind_of_similar = 0
    for (gt_name, gt_seq), (gt_locus, stacks_loci) in secondary_assembly.items():
        if stacks_loci:
            print(stacks_loci)
            kind_of_similar += 1
    print("Of the {} unassigned loci in the first pass (similarity = {}), {} could be assigned with similarity = {}".format(nr_unassigned, 0.2, kind_of_similar, secondary_sim))

    # Write secondary assembly to file
    if args.output:
        with open(args.output, "a") as outfile:
            print("\n"*20, file=outfile)
            print("Secondary assembly with similarity", file=outfile)
            print("\n", file=outfile)
            for (gt_name, gt_seq), (gt_locus, stacks_loci) in secondary_assembly.items():
                if stacks_loci:
                    print(gt_name, file=outfile)
                    print("      ", gt_seq, file=outfile)
                    for record in stacks_loci:
                        print("{:>6}".format(record.locus_id), record.seq, file=outfile)
                    print("\n", file=outfile)


def compensate_rc(mut):
    """Normalize SNPs and call mutation type.

    Consensus and mutation might not be clear so if the
    simulation was C>T, a call of T>C is considered valid.

    Returns:
        tuple: Type ("SNP" or "Indel") and (base_from, base_to) for SNPs, None for indels
    """
    pos_str, mut_str = mut.split(":")
    read, pos = pos_str.split("@")
    if ">" in mut_str:
        base_from, base_to = mut_str.split(">")
        snp_pos = int(pos)
        if read == "p7":
            base_from = dp.complement(base_from)
            base_to = dp.complement(base_to)
            snp_pos = 88 + (81 - snp_pos)## 81 is the read length + the 7 As used for joining and offset of rev comp reverse reads
        return ("SNP", (snp_pos, (base_from, base_to)))
    else:
        return "Indel", None


def decode_stacks_mut(muts):
    """Parse a stacks mutation entry in the TSV file.
    """
    if not muts:
        return []
    all_snps = []
    for mutation_entry in muts:
        if not mutation_entry:
            continue
        mut_strs = mutation_entry.split(";")
        for mut_str in mut_strs:
            pos, bases = mut_str.split(",")
            base_from, base_to = bases.split(">")
            all_snps.append((int(pos), (base_from, base_to)))
    return all_snps
    # return list(set(all_snps))


def merge_snps(snp_list):
    """Merge similar SNPs that are close to each other.
    
    This might be the case due to slight desynchronization
    of the locus assmebly etc.
    """

    def find_close(target, pos, base_from, base_to):
        for t_pos, (t_from, t_to) in target:
            if (t_from == base_from and t_to == base_to) or (t_from == base_to and t_to == base_from):
                if abs(t_pos - pos) < 0:
                    return True
        return False

    merged_list = []
    for snp_pos, (snp_from, snp_to) in snp_list:
        if not find_close(merged_list, snp_pos, snp_from, snp_to):
            merged_list.append((snp_pos, (snp_from, snp_to)))
    if list(sorted(snp_list)) != list(sorted(merged_list)):
        print("merged", snp_list, "--->", merged_list)
    
    return merged_list


def is_close(pos1, pos2):
    """Consider mutations that are less than 10 positions apart as close.

    This encompasses variation by spacer length and indels as well as trimming by stacks.
    """
    if abs(pos1 - pos2) <= 10:
        return True
    else:
        return False
    

def evaluate_snps(assembly, gt_data, stacks_data, args):
    """Evaluate the SNps called by Stacks.
    """
    all_matching = []
    recount_snps = 0
    recount_indels = 0
    count_matched = 0
    unmatched = 0
    unmatched_stacks_mut = 0
    discoverable_snps = 0
    if args.output:
        outfile = open(args.output, "a")
        print("\n"*20, file=outfile)

    # iterate over all simulated loci
    for (gt_name, gt_seq), (gt_locus, stacks_loci) in assembly.items():
        if gt_locus.mutations:
            if args.output:
                print(gt_locus.mutations, "--", "  ".join([locus.snps for locus in stacks_loci]), file=outfile)
            if stacks_loci:
                discoverable_snps += len(gt_locus.mutations)
            matching = []

            all_stacks_muts = decode_stacks_mut([locus.snps for locus in stacks_loci])
            all_rage_snps = []

            for mutation in gt_locus.mutations:
                # normalize for reverse complement, since Stacks saves the reverse reads as rev comp
                mut_type, rage_mut = compensate_rc(mutation)
                # Stacks can only identify SNPs, so ignore Indels
                if mut_type == "SNP":
                    recount_snps += 1
                    all_rage_snps.append(rage_mut)
                else:
                    recount_indels += 1
                    # count unmatched tsacks mutations here
                    unmatched_stacks_mut += len(stacks_muts)

            remaining_stacks_mutations = [mut for mut in all_stacks_muts]

            for rage_pos, rage_snp in all_rage_snps:
                matched = False
                found = []
                not_found = []

                for stacks_mut_pos, stacks_mut in remaining_stacks_mutations:
                    if rage_snp == stacks_mut and is_close(stacks_mut_pos, rage_pos):
                        # matched in the right order
                        matching.append((stacks_mut_pos, rage_snp))
                        found.append((stacks_mut_pos, stacks_mut, rage_pos, rage_snp))
                        matched = True
                        count_matched += 1
                    elif rage_snp == tuple(reversed(stacks_mut)) and is_close(stacks_mut_pos, rage_pos):
                        # matched as inverse order (consensus and mutation might not be clear
                        # so if the simulation was C>T, a Call of T>C is considered valid)
                        matching.append((stacks_mut_pos, rage_snp))
                        found.append((stacks_mut_pos, stacks_mut, rage_pos, rage_snp))
                        matched = True
                        count_matched += 1
                    else:
                        not_found.append((stacks_mut_pos, stacks_mut))
                remaining_stacks_mutations = not_found
                if not matched:
                    unmatched += 1
            
                # use this to check if the found matches are reasonable
                # for found_match in found:
                #     print(found_match)
                #     input()
            if remaining_stacks_mutations:
                # This stacks mutations could not be found in the RAGE reference.
                # It is most likely due to seqeuencing errors.
                print("!! unmatched Stacks mutations", remaining_stacks_mutations, "could not be found in ", all_rage_snps, "in rage locus {}, stacks locus {}".format(gt_name, ", ".join([loc.locus_id for loc in stacks_loci])))
                unmatched_stacks_mut += len(remaining_stacks_mutations)

            all_matching.append((gt_name, matching))
        else:
            
            # count SNPs reported by Stacks for loci without any mutations
            stacks_muts = decode_stacks_mut([locus.snps for locus in stacks_loci])
            if stacks_muts:
                print("Found stacks mut:", stacks_muts)
            unmatched_stacks_mut += len(stacks_muts)

    print("Recounted SNPs:", recount_snps)
    print("Discoverable SNPs:", discoverable_snps)
    print("Recounted Indels:", recount_indels)
    print("identified a total number of {} matching SNPs ({:.2f}%)".format(count_matched, 100*(count_matched/recount_snps)))
    print("identified {} / {} matching SNPs ({:.2f}%) SNPs the the loci identified by stacks".format(count_matched, discoverable_snps, 100*(count_matched/discoverable_snps)))
    print("{} SNPs simulated by RAGE were not identified by stacks ({:.2f}%)".format(unmatched, 100*(unmatched/recount_snps)))
    print("{} SNPs identified by Stacks were not simulated by RAGE".format(unmatched_stacks_mut))
    if args.output:
        outfile.close()



def main(args):
    # print(f"Loading gt data", file=sys.stderr)
    # gt_data = parse_rage_gt_file(args)
    print(f"Loading stacks data", file=sys.stderr)
    stacks_data = get_stacks_data(args)
    # print("Analyzing:", file=sys.stderr)
    # assembly = compare_assemblies(gt_data, stacks_data)
    # print("\n\nLocus Analysis:\n", file=sys.stderr)
    # evaluate_assembly(assembly, gt_data, stacks_data, args)
    # print("\n\nSNPs Analysis:\n", file=sys.stderr)
    # evaluate_snps(assembly, gt_data, stacks_data, args)



def get_argparser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-o", "--output",
        help="If an output file should be written",
        default=False,
        )
    parser.add_argument(
        "-g", "-y", "--ground-truth", "--yaml",
        help="Path the a YAML gt file",
        default="RAGEdataset_ATCACG_gt.yaml",
        dest="yaml",
        )
    parser.add_argument(
        "-s", "--stacks-snps-file",
        help="Path to a stacks snps vcf file",
        dest="stacks_haplo",
        )
    parser.add_argument(
        "-f", "--stacks-fasta-file",
        help="Path to a stacks catalog fasta file",
        dest="stacks_fa",
        )
    return parser


if __name__ == '__main__':
    parser = get_argparser()
    args = parser.parse_args()
    main(args)
