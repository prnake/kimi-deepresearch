from kimi_deepresearch import deep_research
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("-q", "--query", type=str, required=True)
args = parser.parse_args()

result = deep_research(args.query)
print(result)