#Bug 1
def buggy_function(x):
    # LOGICAL BUG: This condition is reversed
    if x < 0:
        return "Positive"
    else:
        return "Negative"

def test_buggy_function():
    # We expect "Negative" when x is -5
    assert buggy_function(-5) == "Negative"
    print("Test passed for bug2!")




#Bug 2
def buggy_function2(y)
        if y>0:
             return "Negative"
        else:
             return "Positive"
    

def test_buggy_function2():
     assert buggy_function2(7) == "Negative"
     print("Test passed for bug2")


#Bug 3




