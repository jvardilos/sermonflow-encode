
# Take from a native google cloud storage and put into a in/ directory
from decode import decode


def main():
    decode("message.probundle", "output", False)
    

if __name__ == "__main__":
    main()