import argparse
import itertools

def main(args):
    with open(args.input, "r") as infile:
        # get last six lines
        last_lines = [infile.__next__() for _ in range(8)]
        for line in infile:
            last_lines.pop(0)
            last_lines.append(line)
    _, discovered, _, of_total, _, undiscovered, _, ratio = last_lines
    print(int(discovered.strip()), int(of_total.strip()), int(undiscovered.strip()), float(ratio.strip()))


def get_argparser():
    """Manage user parameters"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        help="evaluation file generated by evaluate_stacks_results.py",
        dest="input",
    )
    parser.add_argument(
        "--ratio-equals",
        help="Fails, if ratio differs from given parameter",
        dest="ratio_equals",
    )
    return parser


if __name__ == '__main__':
    parser = get_argparser()
    args = parser.parse_args()
    main(args)
