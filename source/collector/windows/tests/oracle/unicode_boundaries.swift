import Foundation
var pictographic: [UInt32] = [], consonant: [UInt32] = [], linker: [UInt32] = [], conjunctExtend: [UInt32] = []
for value in 0...0x10ffff {
 guard let scalar=UnicodeScalar(value) else { continue }
 let s=String(scalar), extends=("a"+s).count==1
 if !extends && ("👩‍"+s).count==1 { pictographic.append(scalar.value) }
 if !extends && ("क्"+s).count==1 { consonant.append(scalar.value) }
 if ("क"+s+"क").count==1 { linker.append(scalar.value) }
 if ("क्"+s+"क").count==1 { conjunctExtend.append(scalar.value) }
}
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["pictographic":pictographic,"consonant":consonant,"linker":linker,"conjunctExtend":conjunctExtend]))
