# sample_code.py
import os
from datetime import datetime

def calculate_area(radius):
    return 3.14 * radius * radius

class DataProcessor:
    def __init__(self, data):
        self.data = data
        
    def process(self):
        return [x * 2 for x in self.data]

def main():
    print("Running main")