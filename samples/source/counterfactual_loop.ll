
define i64 @compute(i64 %n) {
entry:
  br label %loop

loop:
  %i = phi i64 [ 0, %entry ], [ %next, %loop ]
  %sum = phi i64 [ 0, %entry ], [ %sum_next, %loop ]
  %value = add i64 %i, 0
  %sum_next = add i64 %sum, %value
  %next = add i64 %i, 1
  %continue = icmp slt i64 %next, %n
  br i1 %continue, label %loop, label %exit

exit:
  ret i64 %sum_next
}

define i32 @main() {
entry:
  %result = call i64 @compute(i64 10000000)
  %check = icmp eq i64 %result, 49999995000000
  br i1 %check, label %success, label %failure

success:
  ret i32 0

failure:
  ret i32 1
}
