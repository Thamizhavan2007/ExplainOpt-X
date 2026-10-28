int add_zero(int x) { return x + 0; }
int sub_zero(int x) { return x - 0; }
int multiply_one(int x) { return x * 1; }
int divide_one(int x) { return x / 1; }

int constant_folding(void) { return 10 * 20; }
int constant_propagation(void) {
    int x = 10;
    return x + 5;
}
int dead_assignment(void) {
    int x = 5;
    x = 10;
    return x;
}
int common_subexpression(int x) {
    int a = x * 2;
    int b = x * 2;
    return a + b;
}
int strength_reduction(int x) { return x * 8; }
int conditional_simplification(int x) {
    if (x > 0) return 1;
    else return 1;
}
int loop_optimization(void) {
    int sum = 0;
    for (int i = 0; i < 5; i++) sum += i;
    return sum;
}
static int helper(int x) { return x + 3; }
int function_inlining(int x) { return helper(x); }