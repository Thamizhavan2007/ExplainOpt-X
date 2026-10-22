#include <stdio.h>
int main(void) {
    long long sum = 0;
    for (int i = 1; i <= 5000000; i++)
        sum += (long long)i * i;
    printf("%lld\n", sum);
    return 0;
}
